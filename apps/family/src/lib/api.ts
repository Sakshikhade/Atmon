import {
  CLASSES,
  CONSENT_PURPOSE,
  type CaptureAsk,
  type Channels,
  type ClassKey,
  type ConsentKey,
  type Decision,
  type FamilyData,
  type FamilyEvent,
  type FamilySession,
  type GrantRow,
} from "./model"
import { supabase } from "./supabase"

function isClassKey(value: string): value is ClassKey {
  return value in CLASSES
}

function asChannels(value: string): Channels {
  if (value === "video" || value === "audio") return value
  return "both"
}

export async function loadFamily(userId: string): Promise<FamilyData> {
  const profile = await supabase
    .from("profiles")
    .select("display_name")
    .eq("user_id", userId)
    .single()
  if (profile.error) throw new Error(profile.error.message)

  const membersRes = await supabase
    .from("household_members")
    .select("household_id, user_id, role, label, can_record")
  if (membersRes.error) throw new Error(membersRes.error.message)
  const members = membersRes.data ?? []
  const mine = members.find((row) => row.user_id === userId)
  if (!mine) throw new Error("This account is not in a household yet.")

  const guards = await supabase
    .from("child_guardianships")
    .select("child_id, household_id, ended_at")
    .eq("household_id", mine.household_id)
    .is("ended_at", null)
  if (guards.error) throw new Error(guards.error.message)
  const childId = guards.data?.[0]?.child_id as string | undefined
  if (!childId) throw new Error("This household has no child record yet.")

  const child = await supabase.from("children").select("display_name").eq("id", childId).single()
  if (child.error) throw new Error(child.error.message)

  const [sessionsRes, eventsRes, notesRes, trackedRes, grantsRes, asksRes, consentsRes, retentionRes, accessRes] =
    await Promise.all([
      supabase
        .from("sessions")
        .select(
          "id, child_id, household_id, started_at, duration_ms, pre_roll_ms, setting, channels, obscured, antecedent_note, child_aware",
        )
        .eq("household_id", mine.household_id)
        .order("started_at", { ascending: false }),
      supabase
        .from("events")
        .select(
          "id, session_id, class_key, onset_ms, duration_ms, confidence_band, status, source, channels, flagged, corrected_class_key, media_suppressed",
        )
        .eq("child_id", childId),
      supabase.from("notes").select("event_id, body"),
      supabase.from("child_tracked_behaviors").select("class_key, is_target").eq("child_id", childId),
      supabase
        .from("share_grants")
        .select(
          "id, invite_email, clinician_role, clinician_display_name, scope, expires_at, export_allowed, download_allowed, status",
        )
        .eq("household_id", mine.household_id),
      supabase.from("capture_requests").select("id, grant_id, what, setting, status"),
      supabase
        .from("consents")
        .select("purpose, granted, created_at")
        .eq("household_id", mine.household_id)
        .order("created_at", { ascending: false }),
      supabase
        .from("retention_policies")
        .select("default_location, keep_days")
        .eq("household_id", mine.household_id)
        .maybeSingle(),
      supabase.from("access_log").select("grant_id, action"),
    ])

  for (const result of [
    sessionsRes,
    eventsRes,
    notesRes,
    trackedRes,
    grantsRes,
    asksRes,
    consentsRes,
    retentionRes,
    accessRes,
  ]) {
    if (result.error) throw new Error(result.error.message)
  }

  const notes = new Map<string, string>()
  for (const note of notesRes.data ?? []) {
    if (note.event_id && !notes.has(note.event_id)) notes.set(note.event_id, note.body)
  }

  const eventsBySession = new Map<string, FamilyEvent[]>()
  for (const row of eventsRes.data ?? []) {
    if (!isClassKey(row.class_key)) continue
    const event: FamilyEvent = {
      id: row.id,
      classKey: row.class_key,
      onsetMs: row.onset_ms,
      durationMs: row.duration_ms,
      confidence: row.confidence_band,
      status: row.status,
      source: row.source === "family" ? "family" : "system",
      channels: asChannels(row.channels),
      flagged: row.flagged,
      note: notes.get(row.id) ?? "",
      correctedKey: row.corrected_class_key && isClassKey(row.corrected_class_key) ? row.corrected_class_key : null,
      mediaSuppressed: row.media_suppressed === true,
    }
    const list = eventsBySession.get(row.session_id) ?? []
    list.push(event)
    eventsBySession.set(row.session_id, list)
  }

  const sessions: FamilySession[] = (sessionsRes.data ?? []).map((row) => ({
    id: row.id,
    childId: row.child_id,
    householdId: row.household_id,
    startedAt: row.started_at,
    durationMs: row.duration_ms,
    setting: row.setting,
    channels: asChannels(row.channels),
    obscured: row.obscured,
    antecedentNote: row.antecedent_note,
    childAware: row.child_aware,
    preRollMs: row.pre_roll_ms ?? 0,
    events: (eventsBySession.get(row.id) ?? []).sort((a, b) => a.onsetMs - b.onsetMs),
  }))

  const counts = new Map<string, { viewed: number; exported: number; downloaded: number }>()
  for (const row of accessRes.data ?? []) {
    const current = counts.get(row.grant_id) ?? { viewed: 0, exported: 0, downloaded: 0 }
    if (row.action === "viewed" || row.action === "played") current.viewed += 1
    if (row.action === "exported") current.exported += 1
    if (row.action === "downloaded") current.downloaded += 1
    counts.set(row.grant_id, current)
  }

  const grants: GrantRow[] = (grantsRes.data ?? []).map((row) => {
    const tally = counts.get(row.id) ?? { viewed: 0, exported: 0, downloaded: 0 }
    return {
      id: row.id,
      inviteEmail: row.invite_email,
      role: row.clinician_role,
      displayName: row.clinician_display_name,
      scope: row.scope,
      expiresAt: row.expires_at,
      exportAllowed: row.export_allowed,
      downloadAllowed: row.download_allowed,
      status: row.status,
      ...tally,
    }
  })

  const grantById = new Map(grants.map((grant) => [grant.id, grant]))
  const asks: CaptureAsk[] = (asksRes.data ?? [])
    .filter((row) => row.status === "open")
    .map((row) => {
      const grant = grantById.get(row.grant_id)
      return {
        id: row.id,
        what: row.what,
        setting: row.setting,
        role: grant?.role ?? "other",
        name: grant?.displayName ?? "",
      }
    })

  const consents: FamilyData["consents"] = {
    backup: false,
    recordings: false,
    corrections: true,
    analytics: false,
  }
  const seen = new Set<string>()
  for (const row of consentsRes.data ?? []) {
    if (seen.has(row.purpose)) continue
    seen.add(row.purpose)
    const key = (Object.entries(CONSENT_PURPOSE).find(([, purpose]) => purpose === row.purpose)?.[0] ??
      null) as ConsentKey | null
    if (key) consents[key] = row.granted
  }

  return {
    userId,
    displayName: profile.data.display_name,
    childId,
    childName: child.data.display_name,
    householdId: mine.household_id,
    members: members
      .filter((row) => row.household_id === mine.household_id)
      .map((row) => ({ userId: row.user_id, role: row.role, label: row.label })),
    sessions,
    tracked: (trackedRes.data ?? [])
      .filter((row) => isClassKey(row.class_key))
      .map((row) => ({ key: row.class_key as ClassKey, isTarget: row.is_target })),
    grants,
    asks,
    consents,
    retention: {
      where: retentionRes.data?.default_location === "cloud" ? "cloud" : "device",
      days: retentionRes.data?.keep_days ?? 90,
    },
    retentionSaved: Boolean(retentionRes.data),
    canRecord: mine.can_record !== false,
  }
}

export async function verifyEvent(
  userId: string,
  eventId: string,
  decision: Decision,
  correctedKey: ClassKey | null,
): Promise<string | null> {
  const { error } = await supabase.from("event_verifications").insert({
    event_id: eventId,
    actor_id: userId,
    actor_kind: "family",
    decision,
    corrected_class_key: correctedKey,
  })
  return error?.message ?? null
}

export async function setFlag(eventId: string, flagged: boolean, userId: string): Promise<string | null> {
  const { error } = await supabase
    .from("events")
    .update({ flagged, flagged_by: flagged ? userId : null })
    .eq("id", eventId)
  return error?.message ?? null
}

export async function addNote(userId: string, eventId: string, body: string): Promise<string | null> {
  const { error } = await supabase.from("notes").insert({
    event_id: eventId,
    author_id: userId,
    body,
    to_family: false,
  })
  return error?.message ?? null
}

export async function addSeenEvent(input: {
  session: FamilySession
  classKey: ClassKey
  onsetMs: number
}): Promise<string | null> {
  const id = crypto.randomUUID()
  const inserted = await supabase.from("events").insert({
    id,
    session_id: input.session.id,
    child_id: input.session.childId,
    class_key: input.classKey,
    onset_ms: input.onsetMs,
    duration_ms: 10000,
    confidence_band: null,
    source: "family",
    channels: input.session.channels,
    status: "detected",
  })
  if (inserted.error) return inserted.error.message
  const { data: userData } = await supabase.auth.getUser()
  const userId = userData.user?.id
  if (!userId) return "Sign in again to add this event."
  return verifyEvent(userId, id, "confirm", null)
}

export async function saveSessionDetails(input: {
  sessionId: string
  setting: string | null
  antecedentNote: string | null
  childAware: "yes" | "not_really" | null
}): Promise<string | null> {
  const { error } = await supabase
    .from("sessions")
    .update({
      setting: input.setting,
      antecedent_note: input.antecedentNote,
      child_aware: input.childAware,
    })
    .eq("id", input.sessionId)
  return error?.message ?? null
}

export async function createGrant(input: {
  householdId: string
  childId: string
  userId: string
  email: string
  role: string
  displayName: string
  scope: GrantRow["scope"]
  expiresAt: string
  downloadAllowed: boolean
  items: { eventId: string; startMs: number; endMs: number }[]
}): Promise<string | null> {
  const id = crypto.randomUUID()
  const grant = await supabase.from("share_grants").insert({
    id,
    household_id: input.householdId,
    child_id: input.childId,
    invite_email: input.email,
    clinician_role: input.role,
    clinician_display_name: input.displayName,
    scope: input.scope,
    expires_at: input.expiresAt,
    export_allowed: true,
    download_allowed: input.downloadAllowed,
    download_ack_at: input.downloadAllowed ? new Date().toISOString() : null,
    status: "pending",
    created_by: input.userId,
  })
  if (grant.error) return grant.error.message
  if (input.items.length === 0) return null
  const items = await supabase.from("share_grant_items").insert(
    input.items.map((item) => ({
      grant_id: id,
      event_id: item.eventId,
      clip_start_ms: item.startMs,
      clip_end_ms: item.endMs,
    })),
  )
  return items.error?.message ?? null
}

export async function updateGrant(
  id: string,
  patch: Partial<{
    scope: GrantRow["scope"]
    expires_at: string
    export_allowed: boolean
    download_allowed: boolean
    download_ack_at: string | null
    status: GrantRow["status"]
    ended_at: string | null
    ended_by: string | null
  }>,
): Promise<string | null> {
  const { error } = await supabase.from("share_grants").update(patch).eq("id", id)
  return error?.message ?? null
}

export async function saveConsent(
  householdId: string,
  userId: string,
  key: ConsentKey,
  granted: boolean,
): Promise<string | null> {
  const { error } = await supabase.from("consents").insert({
    household_id: householdId,
    user_id: userId,
    purpose: CONSENT_PURPOSE[key],
    granted,
    text_version: "ux-1",
  })
  return error?.message ?? null
}

export async function saveRetention(
  householdId: string,
  where: "device" | "cloud",
  days: number,
  exists: boolean,
): Promise<string | null> {
  const row = { household_id: householdId, default_location: where, keep_days: days, promote_flagged: true }
  const result = exists
    ? await supabase.from("retention_policies").update(row).eq("household_id", householdId)
    : await supabase.from("retention_policies").insert(row)
  return result.error?.message ?? null
}

export async function declineAsk(id: string): Promise<string | null> {
  const { error } = await supabase.from("capture_requests").update({ status: "declined" }).eq("id", id)
  return error?.message ?? null
}

export type CaptureSegment = {
  seq: number
  startMs: number
  durationMs: number
  sha256: string | null
}

export type CaptureEvent = {
  id: string
  classKey: ClassKey
  onsetMs: number
  durationMs: number
  confidence: "confident" | "needs_a_look"
  channels: Channels
}

export type CapturePayload = {
  sessionId: string
  childId: string
  householdId: string
  startedAt: string
  durationMs: number
  preRollMs: number
  channels: Channels
  obscured: boolean
  events: CaptureEvent[]
  segments: CaptureSegment[]
  suppressed: { id: string; classKey: ClassKey; startMs: number; endMs: number }[]
  assent: { id: string; kind: "paused_by_child" | "resumed"; atMs: number }[]
}

const OUTBOX_KEY = "atmon-capture-outbox"

function alreadyThere(error: { code?: string; message: string } | null): boolean {
  if (!error) return false
  return error.code === "23505" || /duplicate key/i.test(error.message)
}

export async function saveCapture(input: CapturePayload): Promise<string | null> {
  if (typeof navigator !== "undefined" && navigator.onLine === false) return "offline"
  const { data: userData } = await supabase.auth.getUser()
  const userId = userData.user?.id
  if (!userId) return "Sign in again to save this session."

  const session = await supabase.from("sessions").insert({
    id: input.sessionId,
    child_id: input.childId,
    household_id: input.householdId,
    recorded_by: userId,
    started_at: input.startedAt,
    duration_ms: input.durationMs,
    pre_roll_ms: input.preRollMs,
    channels: input.channels,
    obscured: input.obscured,
    storage_location: "device",
    processing_status: "ready",
    detector_version: "stub",
    source: "handheld",
  })
  if (session.error && !alreadyThere(session.error)) return session.error.message

  if (input.segments.length > 0) {
    const segments = await supabase.from("session_segments").insert(
      input.segments.map((segment) => ({
        session_id: input.sessionId,
        seq: segment.seq,
        start_ms: segment.startMs,
        duration_ms: segment.durationMs,
        sha256: segment.sha256,
        storage_path: `idb:${input.sessionId}`,
      })),
    )
    if (segments.error && !alreadyThere(segments.error)) return segments.error.message
  }

  if (input.events.length > 0) {
    const events = await supabase.from("events").insert(
      input.events.map((event) => ({
        id: event.id,
        session_id: input.sessionId,
        child_id: input.childId,
        class_key: event.classKey,
        onset_ms: event.onsetMs,
        duration_ms: event.durationMs,
        confidence_band: event.confidence,
        source: "system",
        channels: event.channels,
        status: "detected",
        detector_version: "stub",
      })),
    )
    if (events.error && !alreadyThere(events.error)) return events.error.message
  }

  const suppressed = input.suppressed ?? []
  const assentMarks = input.assent ?? []

  if (suppressed.length > 0) {
    const removed = await supabase.from("events").insert(
      suppressed.map((range) => ({
        id: range.id,
        session_id: input.sessionId,
        child_id: input.childId,
        class_key: range.classKey,
        onset_ms: range.startMs,
        duration_ms: Math.max(1, range.endMs - range.startMs),
        confidence_band: null,
        source: "family",
        channels: input.channels,
        status: "detected",
        media_suppressed: true,
      })),
    )
    if (removed.error && !alreadyThere(removed.error)) return removed.error.message
  }

  if (assentMarks.length > 0) {
    const assent = await supabase.from("assent_events").insert(
      assentMarks.map((mark) => ({
        id: mark.id,
        session_id: input.sessionId,
        kind: mark.kind,
        at_ms: mark.atMs,
      })),
    )
    if (assent.error && !alreadyThere(assent.error)) return assent.error.message
  }

  return null
}

function readOutbox(): CapturePayload[] {
  const raw = localStorage.getItem(OUTBOX_KEY)
  if (!raw) return []
  try {
    const parsed = JSON.parse(raw) as CapturePayload[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function queueOutbox(item: CapturePayload) {
  const items = readOutbox().filter((existing) => existing.sessionId !== item.sessionId)
  items.push(item)
  localStorage.setItem(OUTBOX_KEY, JSON.stringify(items))
}

export async function flushOutbox(): Promise<void> {
  const items = readOutbox()
  if (items.length === 0) return
  const left: CapturePayload[] = []
  for (const item of items) {
    const error = await saveCapture(item)
    if (error) left.push(item)
  }
  if (left.length > 0) localStorage.setItem(OUTBOX_KEY, JSON.stringify(left))
  else localStorage.removeItem(OUTBOX_KEY)
}

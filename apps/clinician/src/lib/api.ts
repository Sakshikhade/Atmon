import type { ClassKey, ClinData, ClinEvent, ClinSession, Decision } from "./model"
import { isClassKey } from "./model"
import { supabase } from "./supabase"

export async function acceptInvites(displayName: string): Promise<string | null> {
  const { error } = await supabase.rpc("accept_invites", { display_name: displayName })
  return error?.message ?? null
}

export async function loadClinician(userId: string): Promise<ClinData> {
  const profile = await supabase.from("profiles").select("display_name").eq("user_id", userId).maybeSingle()
  if (profile.error) throw new Error(profile.error.message)
  const clin = await supabase.from("clinician_profiles").select("role, org_name, verified_at").eq("user_id", userId).maybeSingle()
  if (clin.error) throw new Error(clin.error.message)

  const grantsRes = await supabase
    .from("share_grants")
    .select("id, child_id, clinician_role, scope, expires_at, download_allowed, export_allowed, status")
    .eq("clinician_id", userId)
    .eq("status", "active")
  if (grantsRes.error) throw new Error(grantsRes.error.message)
  const grantRows = grantsRes.data ?? []
  const childIds = [...new Set(grantRows.map((row) => row.child_id))]

  const [childrenRes, sessionsRes, eventsRes, notesRes, verificationsRes, antecedentsRes, requestsRes, itemsRes] = await Promise.all([
    childIds.length
      ? supabase.from("children").select("id, display_name").in("id", childIds)
      : Promise.resolve({ data: [], error: null }),
    supabase.from("sessions").select("id, child_id, started_at, duration_ms, pre_roll_ms, setting, antecedent_note, storage_location"),
    supabase.from("events").select("id, session_id, child_id, class_key, onset_ms, duration_ms, confidence_band, status, flagged, media_suppressed"),
    supabase.from("notes").select("event_id, body, to_family"),
    supabase.from("event_verifications").select("event_id, actor_id, actor_kind, decision, created_at").eq("actor_id", userId),
    supabase.from("event_antecedents").select("event_id, text"),
    supabase.from("capture_requests").select("id, grant_id, what, setting, status").eq("clinician_id", userId),
    grantRows.length
      ? supabase.from("share_grant_items").select("grant_id, event_id").in("grant_id", grantRows.map((row) => row.id))
      : Promise.resolve({ data: [], error: null }),
  ])
  for (const result of [childrenRes, sessionsRes, eventsRes, notesRes, verificationsRes, antecedentsRes, requestsRes, itemsRes]) {
    if (result.error) throw new Error(result.error.message)
  }

  const names = new Map((childrenRes.data ?? []).map((row) => [row.id, row.display_name as string]))
  const familyNotes = new Map<string, string>()
  for (const note of notesRes.data ?? []) {
    if (note.event_id && note.to_family === false && !familyNotes.has(note.event_id)) familyNotes.set(note.event_id, note.body)
  }
  const decisions = new Map<string, Decision>()
  const sorted = [...(verificationsRes.data ?? [])].sort((a, b) => (a.created_at < b.created_at ? 1 : -1))
  for (const row of sorted) {
    if (row.actor_kind === "clinician" && !decisions.has(row.event_id) && (row.decision === "confirm" || row.decision === "correct" || row.decision === "reject")) {
      decisions.set(row.event_id, row.decision)
    }
  }
  const antecedents = new Map((antecedentsRes.data ?? []).map((row) => [row.event_id, row.text as string]))
  const coveredChildren = new Set(childIds)
  const openChildren = new Set(grantRows.filter((row) => row.scope === "all").map((row) => row.child_id))
  const sharedEvents = new Set((itemsRes.data ?? []).map((row) => row.event_id))

  const eventsBySession = new Map<string, ClinEvent[]>()
  for (const row of eventsRes.data ?? []) {
    if (!isClassKey(row.class_key)) continue
    if (!coveredChildren.has(row.child_id)) continue
    if (!openChildren.has(row.child_id) && !sharedEvents.has(row.id)) continue
    const event: ClinEvent = {
      id: row.id,
      sessionId: row.session_id,
      childId: row.child_id,
      classKey: row.class_key,
      onsetMs: row.onset_ms,
      durationMs: row.duration_ms,
      confidence: row.confidence_band,
      status: row.status,
      flagged: row.flagged,
      note: familyNotes.get(row.id) ?? "",
      mediaSuppressed: row.media_suppressed === true,
      clinicianDecision: decisions.get(row.id) ?? null,
      antecedent: antecedents.get(row.id) ?? null,
    }
    const list = eventsBySession.get(row.session_id) ?? []
    list.push(event)
    eventsBySession.set(row.session_id, list)
  }

  const sessions: ClinSession[] = (sessionsRes.data ?? [])
    .filter((row) => coveredChildren.has(row.child_id))
    .map((row) => ({
      id: row.id,
      childId: row.child_id,
      startedAt: row.started_at,
      durationMs: row.duration_ms,
      preRollMs: row.pre_roll_ms ?? 0,
      setting: row.setting,
      antecedentNote: row.antecedent_note,
      storageLocation: (row.storage_location === "cloud" ? "cloud" : "device") as "cloud" | "device",
      events: (eventsBySession.get(row.id) ?? []).sort((a, b) => a.onsetMs - b.onsetMs),
    }))
    .filter((session) => openChildren.has(session.childId) || session.events.length > 0)

  return {
    userId,
    displayName: profile.data?.display_name ?? "Clinician",
    role: clin.data?.role ?? null,
    orgName: clin.data?.org_name ?? null,
    verifiedAt: clin.data?.verified_at ?? null,
    grants: grantRows.map((row) => ({
      id: row.id,
      childId: row.child_id,
      childName: names.get(row.child_id) ?? "Child",
      role: row.clinician_role,
      scope: row.scope,
      expiresAt: row.expires_at,
      downloadAllowed: row.download_allowed,
      exportAllowed: row.export_allowed,
      status: row.status,
    })),
    sessions,
    requests: (requestsRes.data ?? []).map((row) => ({
      id: row.id,
      grantId: row.grant_id,
      childName: names.get(grantRows.find((grant) => grant.id === row.grant_id)?.child_id ?? "") ?? "Child",
      what: row.what,
      setting: row.setting,
      status: row.status,
    })),
  }
}

export async function judgeEvent(userId: string, eventId: string, decision: Decision, classKey: ClassKey | null): Promise<string | null> {
  const { error } = await supabase.from("event_verifications").insert({
    event_id: eventId,
    actor_id: userId,
    actor_kind: "clinician",
    decision,
    corrected_class_key: classKey,
  })
  return error?.message ?? null
}

export async function saveAntecedent(userId: string, eventId: string, text: string): Promise<string | null> {
  const { error } = await supabase.from("event_antecedents").insert({
    event_id: eventId,
    clinician_id: userId,
    text,
  })
  return error?.message ?? null
}

export async function sendFamilyNote(userId: string, eventId: string, body: string): Promise<string | null> {
  const { error } = await supabase.from("notes").insert({
    event_id: eventId,
    author_id: userId,
    body,
    to_family: true,
  })
  return error?.message ?? null
}

export async function askCapture(input: { grantId: string; userId: string; what: string; setting: string | null; message: string }): Promise<string | null> {
  const { error } = await supabase.from("capture_requests").insert({
    grant_id: input.grantId,
    clinician_id: input.userId,
    what: input.what,
    setting: input.setting,
    message: input.message || null,
    status: "open",
  })
  return error?.message ?? null
}

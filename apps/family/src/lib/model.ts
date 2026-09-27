export type ClassKey = "flap" | "vocal" | "mand" | "away" | "floor"
export type EventStatus = "detected" | "confirmed" | "corrected" | "rejected"
export type Channels = "both" | "video" | "audio"
export type Decision = "confirm" | "correct" | "reject"

export type FamilyEvent = {
  id: string
  classKey: ClassKey
  onsetMs: number
  durationMs: number
  confidence: "confident" | "needs_a_look" | null
  status: EventStatus
  source: "system" | "family"
  channels: Channels
  flagged: boolean
  note: string
  correctedKey: ClassKey | null
}

export type FamilySession = {
  id: string
  childId: string
  householdId: string
  startedAt: string
  durationMs: number
  setting: string | null
  channels: Channels
  obscured: boolean
  antecedentNote: string | null
  childAware: "yes" | "not_really" | null
  events: FamilyEvent[]
}

export type HouseholdMember = {
  userId: string
  role: string
  label: string | null
}

export type GrantRow = {
  id: string
  inviteEmail: string
  role: string
  displayName: string
  scope: "clip" | "flagged" | "session" | "all"
  expiresAt: string
  exportAllowed: boolean
  downloadAllowed: boolean
  status: "pending" | "active" | "ended" | "expired"
  viewed: number
  exported: number
  downloaded: number
}

export type CaptureAsk = {
  id: string
  what: string
  setting: string | null
  role: string
  name: string
}

export type FamilyData = {
  userId: string
  displayName: string
  childId: string
  childName: string
  householdId: string
  members: HouseholdMember[]
  sessions: FamilySession[]
  tracked: { key: ClassKey; isTarget: boolean }[]
  grants: GrantRow[]
  asks: CaptureAsk[]
  consents: Record<ConsentKey, boolean>
  retention: { where: "device" | "cloud"; days: number }
  retentionSaved: boolean
}

export type ConsentKey = "backup" | "recordings" | "corrections" | "analytics"

export const CONSENT_PURPOSE: Record<ConsentKey, string> = {
  backup: "cloud_backup",
  recordings: "train_on_recordings",
  corrections: "train_on_corrections",
  analytics: "product_analytics",
}

export const CLASSES: Record<
  ClassKey,
  { name: string; short: string; color: string; reliability: string }
> = {
  flap: {
    name: "Hand flapping",
    short: "Hand flapping",
    color: "var(--c-flap)",
    reliability: "Usually right. Sometimes misses when hands are out of frame.",
  },
  vocal: {
    name: "Vocal stereotypy",
    short: "Vocal stereotypy",
    color: "var(--c-vocal)",
    reliability: "Usually right. Works on sound pattern, never on words.",
  },
  mand: {
    name: "Communication attempt",
    short: "Communication attempt",
    color: "var(--c-mand)",
    reliability: "Often misses quiet attempts. Worth checking by hand.",
  },
  away: {
    name: "Moving away from caregiver",
    short: "Moving away",
    color: "var(--c-away)",
    reliability: "Sometimes confuses this with play. Check before relying on it.",
  },
  floor: {
    name: "Dropping to floor",
    short: "Dropping to floor",
    color: "var(--c-floor)",
    reliability: "Usually right.",
  },
}

export const CLASS_KEYS = Object.keys(CLASSES) as ClassKey[]

export function kindLabel(key: ClassKey, isTarget: boolean): string {
  if (isTarget) return "Target behaviour, chosen together"
  if (key === "mand") return "Communication"
  return "Self-regulating"
}

export function roleWord(role: string): string {
  if (role === "BCBA") return "BCBA"
  const labels: Record<string, string> = {
    psychologist: "psychologist",
    paediatrician: "paediatrician",
    school_team: "school team",
    therapist: "therapist",
    other: "clinician",
  }
  return labels[role] ?? role.toLowerCase()
}

export function yourClin(role: string | null): string {
  return role ? `your ${roleWord(role)}` : "your clinician"
}

const SETTING_LABEL: Record<string, string> = {
  home_transition: "Home, transition",
  supermarket: "Supermarket",
  playground: "Playground",
  home: "Home",
  shop: "Shop",
  therapy: "Therapy",
  transition: "Transition",
  other: "Other",
}

export function settingLabel(raw: string | null): string {
  if (!raw) return "Session"
  return SETTING_LABEL[raw] ?? raw
}

export function channelsLabel(channels: Channels): string {
  if (channels === "audio") return "Audio only"
  if (channels === "video") return "Video only"
  return "Video and audio"
}

export function mmss(seconds: number): string {
  const t = Math.max(0, Math.round(seconds))
  const m = Math.floor(t / 60)
  const s = t % 60
  return `${m}:${String(s).padStart(2, "0")}`
}

export function durWord(seconds: number): string {
  const d = Math.max(0, Math.round(seconds))
  if (d < 60) return `${d}s`
  return `${Math.floor(d / 60)}m ${d % 60}s`
}

export function confWord(band: FamilyEvent["confidence"]): string {
  if (band === "needs_a_look") return "Needs a look"
  if (band === "confident") return "Confident"
  return "You added this"
}

export function badgeWord(status: EventStatus): string {
  if (status === "detected") return "To check"
  if (status === "confirmed") return "Confirmed"
  if (status === "rejected") return "Not this"
  return "Corrected"
}

export function whenLabel(iso: string): string {
  const d = new Date(iso)
  const now = new Date()
  const start = (x: Date) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
  const day = start(d)
  const today = start(now)
  const name =
    day === today
      ? "Today"
      : day === today - 86400000
        ? "Yesterday"
        : d.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })
  return `${name}, ${time}`
}

export function untilLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  })
}

export const SCOPE_LABEL: Record<GrantRow["scope"], string> = {
  flagged: "Flagged clips, all behaviours",
  clip: "Clips you send, one at a time",
  session: "This session",
  all: "Every session, as recorded",
}

export const LEAD_MS = 30000
export const TAIL_MS = 15000

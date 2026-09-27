export type ClassKey = "flap" | "vocal" | "mand" | "away" | "floor"
export type Decision = "confirm" | "correct" | "reject"

export const CLASSES: Record<ClassKey, { name: string; short: string; color: string; reliability: string; kind: string }> = {
  flap: { name: "Hand flapping", short: "Hand flapping", color: "var(--c-flap)", reliability: "Usually right. Sometimes misses when hands are out of frame.", kind: "Self-regulating" },
  vocal: { name: "Vocal stereotypy", short: "Vocal stereotypy", color: "var(--c-vocal)", reliability: "Usually right. Works on sound pattern, never on words.", kind: "Self-regulating" },
  mand: { name: "Communication attempt", short: "Communication attempt", color: "var(--c-mand)", reliability: "Often misses quiet attempts. Worth checking by hand.", kind: "Communication" },
  away: { name: "Moving away from caregiver", short: "Moving away", color: "var(--c-away)", reliability: "Sometimes confuses this with play. Check before relying on it.", kind: "Self-regulating" },
  floor: { name: "Dropping to floor", short: "Dropping to floor", color: "var(--c-floor)", reliability: "Usually right.", kind: "Self-regulating" },
}

export const CLASS_KEYS = Object.keys(CLASSES) as ClassKey[]

export function isClassKey(value: string): value is ClassKey {
  return value in CLASSES
}

export function roleWord(role: string): string {
  const labels: Record<string, string> = {
    BCBA: "BCBA",
    psychologist: "psychologist",
    paediatrician: "paediatrician",
    school_team: "school team",
    therapist: "therapist",
    other: "clinician",
  }
  return labels[role] ?? role
}

export function settingLabel(raw: string | null): string {
  if (!raw) return "Session"
  const labels: Record<string, string> = { home: "Home", shop: "Shop", therapy: "Therapy", transition: "Transition", playground: "Playground", other: "Other" }
  return labels[raw] ?? raw
}

export function mmss(seconds: number): string {
  const t = Math.max(0, Math.round(seconds))
  return `${Math.floor(t / 60)}:${String(t % 60).padStart(2, "0")}`
}

export function durWord(seconds: number): string {
  const d = Math.max(0, Math.round(seconds))
  if (d < 60) return `${d}s`
  return `${Math.floor(d / 60)}m ${d % 60}s`
}

export function whenLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short" })
}

export function confWord(band: "confident" | "needs_a_look" | null): string {
  if (band === "needs_a_look") return "Needs a look"
  if (band === "confident") return "Confident"
  return "Added by the family"
}

export type ClinEvent = {
  id: string
  sessionId: string
  childId: string
  classKey: ClassKey
  onsetMs: number
  durationMs: number
  confidence: "confident" | "needs_a_look" | null
  status: "detected" | "confirmed" | "corrected" | "rejected"
  flagged: boolean
  note: string
  mediaSuppressed: boolean
  clinicianDecision: Decision | null
  antecedent: string | null
}

export type ClinSession = {
  id: string
  childId: string
  startedAt: string
  durationMs: number
  preRollMs: number
  setting: string | null
  antecedentNote: string | null
  storageLocation: "device" | "cloud"
  events: ClinEvent[]
}

export type ClinGrant = {
  id: string
  childId: string
  childName: string
  role: string
  scope: string
  expiresAt: string
  downloadAllowed: boolean
  exportAllowed: boolean
  status: string
}

export type CaptureRequest = {
  id: string
  grantId: string
  childName: string
  what: string
  setting: string | null
  status: string
}

export type ClinData = {
  userId: string
  displayName: string
  role: string | null
  orgName: string | null
  verifiedAt: string | null
  grants: ClinGrant[]
  sessions: ClinSession[]
  requests: CaptureRequest[]
}

import type { Channels, ClassKey } from "./model"
import { uuidv7 } from "./ids"

export type DraftEvent = {
  id: string
  classKey: ClassKey
  onsetMs: number
  durationMs: number
  confidence: "confident" | "needs_a_look"
  channels: Channels
}

const DEFAULT_CLASSES: ClassKey[] = ["flap", "vocal", "mand", "away", "floor"]

function hash(value: string): number {
  let h = 0
  for (const char of value) h = (Math.imul(h, 31) + char.charCodeAt(0)) | 0
  return h
}

function mulberry32(seed: number) {
  let state = seed
  return () => {
    state = (state + 0x6d2b79f5) | 0
    let t = Math.imul(state ^ (state >>> 15), 1 | state)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

/** Deterministic stand-in for the on-device detector. Same session id, same events. */
export function detectStub(
  sessionId: string,
  durationMs: number,
  preRollMs: number,
  classes: ClassKey[],
): DraftEvent[] {
  const keys = classes.length > 0 ? classes : DEFAULT_CLASSES
  const rand = mulberry32(hash(sessionId))
  const count = durationMs < 8000 ? 1 : Math.min(5, 1 + Math.floor((durationMs - preRollMs) / 25000))
  const window = Math.max(1000, durationMs - preRollMs)
  const events: DraftEvent[] = []
  for (let index = 0; index < count; index += 1) {
    const onset = preRollMs + Math.floor(rand() * Math.max(1, window - 2000))
    const duration = 2000 + Math.floor(rand() * 6000)
    events.push({
      id: uuidv7(),
      classKey: keys[Math.floor(rand() * keys.length)] ?? "flap",
      onsetMs: Math.max(0, Math.min(onset, Math.max(0, durationMs - 1000))),
      durationMs: Math.min(duration, 12000),
      confidence: rand() > 0.4 ? "confident" : "needs_a_look",
      channels: "both",
    })
  }
  return events.sort((a, b) => a.onsetMs - b.onsetMs)
}

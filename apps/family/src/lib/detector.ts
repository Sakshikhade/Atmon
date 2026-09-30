import type { Channels, ClassKey } from "./model"
import { CLASSES } from "./model"
import { uuidv7 } from "./ids"

export type DraftEvent = {
  id: string
  classKey: ClassKey
  onsetMs: number
  durationMs: number
  confidence: "confident" | "needs_a_look"
  channels: Channels
}

export type ActionServiceEvent = {
  class: string
  start_sec: number
  end_sec: number
  score: number
}

export type ActionServiceResponse = {
  detector_version: string
  events: ActionServiceEvent[]
}

export const ACTION_CLASS_KEYS: ClassKey[] = ["ear_cover", "hair_twirling", "head_nodding"]

const DEFAULT_CLASSES: ClassKey[] = ["flap", "vocal", "mand", "away", "floor"]

const SCORE_CONFIDENT = 1.8

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

export function isActionClassKey(value: string): value is ClassKey {
  return value in CLASSES && (ACTION_CLASS_KEYS as string[]).includes(value)
}

/** Map offline action_detection spans onto the family DraftEvent timeline. */
export function mapActionDetections(
  events: ActionServiceEvent[],
  durationMs: number,
  channels: Channels = "video",
  newId: () => string = uuidv7,
): DraftEvent[] {
  const mapped: DraftEvent[] = []
  for (const event of events) {
    if (!isActionClassKey(event.class)) continue
    const startMs = Math.max(0, Math.round(event.start_sec * 1000))
    const endMs = Math.max(startMs + 1, Math.round(event.end_sec * 1000))
    const onsetMs = Math.min(startMs, Math.max(0, durationMs - 1))
    const duration = Math.min(endMs - startMs, Math.max(1, durationMs - onsetMs))
    mapped.push({
      id: newId(),
      classKey: event.class,
      onsetMs,
      durationMs: duration,
      confidence: event.score >= SCORE_CONFIDENT ? "confident" : "needs_a_look",
      channels,
    })
  }
  return mapped.sort((a, b) => a.onsetMs - b.onsetMs)
}

/** Default wait for post-capture scoring before falling back to the local stub. */
export const DETECT_TIMEOUT_MS = 45_000

/**
 * Post-capture call to the local action_detection service.
 * Spans are relative to the uploaded file start (pre-roll already in the blob).
 */
export async function detectWithActionService(
  blob: Blob,
  durationMs: number,
  detectUrl: string,
  channels: Channels = "video",
  timeoutMs: number = DETECT_TIMEOUT_MS,
): Promise<{ events: DraftEvent[]; detectorVersion: string }> {
  const base = detectUrl.replace(/\/$/, "")
  const body = new FormData()
  const type = blob.type || "video/webm"
  body.append("file", blob, type.includes("mp4") ? "capture.mp4" : "capture.webm")

  const controller = new AbortController()
  const timer = window.setTimeout(() => controller.abort(), timeoutMs)
  let res: Response
  try {
    res = await fetch(`${base}/api/detect/video`, {
      method: "POST",
      body,
      signal: controller.signal,
    })
  } catch (error) {
    if (controller.signal.aborted) {
      throw new Error(`detect service timed out after ${timeoutMs}ms`)
    }
    throw error
  } finally {
    window.clearTimeout(timer)
  }
  if (!res.ok) {
    const detail = await res.text().catch(() => "")
    throw new Error(detail || `detect service returned ${res.status}`)
  }
  const payload = (await res.json()) as ActionServiceResponse
  console.info("detect /api/detect/video", payload)
  return {
    events: mapActionDetections(payload.events ?? [], durationMs, channels),
    detectorVersion: payload.detector_version || "xclip-prototypes-v1",
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

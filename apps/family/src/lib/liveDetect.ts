import type { ActionServiceEvent } from "./detector"

const FRAME_INTERVAL_MS = 125
const CONFLICT_STOP_AFTER = 12

type PauseRange = { start: number; end: number }

type LiveSpan = {
  class: string
  start: number
  end: number | null
  score: number
}

export type LiveNotice = {
  event_id?: string
  class?: string
  start?: number | null
  end?: number | null
  score?: number | null
}

/**
 * Map a live-detector timestamp onto the take.
 * Live seconds start when the server loop starts; the recording starts earlier
 * and pauses are missing from the saved video.
 */
export function liveSpanToVideoSec(liveSec: number, offsetSec: number, pauses: PauseRange[]): number {
  const wall = offsetSec + liveSec
  let paused = 0
  for (const range of pauses) {
    const overlap = Math.min(range.end, wall) - Math.max(range.start, 0)
    if (overlap > 0) paused += overlap
  }
  return Math.max(0, wall - paused)
}

/** One row per gesture. A later close replaces the open notice with the same id. */
export function upsertLiveSpan(spans: Map<string, LiveSpan>, notice: LiveNotice, kind: "open" | "close"): void {
  if (!notice.class || notice.start == null) return
  const id = notice.event_id || `${notice.class}-${notice.start}`
  const prev = spans.get(id)
  const end = kind === "close" ? (notice.end ?? prev?.end ?? null) : (prev?.end ?? null)
  const score = kind === "close" || notice.score != null ? Number(notice.score ?? prev?.score ?? 0) : (prev?.score ?? 0)
  spans.set(id, {
    class: notice.class,
    start: prev?.start ?? notice.start,
    end,
    score,
  })
}

export function closedLiveSpans(spans: Iterable<LiveSpan>): Array<{ class: string; start: number; end: number; score: number }> {
  const closed: Array<{ class: string; start: number; end: number; score: number }> = []
  for (const span of spans) {
    if (span.end == null) continue
    closed.push({ class: span.class, start: span.start, end: span.end, score: span.score })
  }
  return closed
}

/**
 * Events that belong to this take. One row per gesture already lives in the map.
 * A gesture still open at Stop is closed at the current live clock.
 */
export function eventsForTake(
  spans: Iterable<LiveSpan>,
  takeOriginSec: number,
  liveNowSec: number,
  pauses: PauseRange[],
): ActionServiceEvent[] {
  const events: ActionServiceEvent[] = []
  for (const span of spans) {
    const end = span.end ?? liveNowSec
    if (end <= takeOriginSec) continue
    const start = Math.max(span.start, takeOriginSec)
    const startSec = liveSpanToVideoSec(start - takeOriginSec, 0, pauses)
    const endSec = liveSpanToVideoSec(end - takeOriginSec, 0, pauses)
    if (endSec <= startSec) continue
    events.push({ class: span.class, start_sec: startSec, end_sec: endSec, score: span.score })
  }
  return events
}

export type LiveDetect = {
  stopPump: () => void
  pause: () => void
  resume: () => void
  finish: () => Promise<ActionServiceEvent[] | null>
  abort: () => void
}

type ActiveSession = {
  base: string
  closed: boolean
  setStatus: (onStatus?: (text: string) => void) => void
  setEvents: (onEvents?: (events: ActionServiceEvent[]) => void) => void
  adoptStream: (stream: MediaStream) => void
  beginTake: () => void
  handle: LiveDetect
}

let active: ActiveSession | null = null

/**
 * Run the same live loop as the demo while a family take is recording.
 * One session is shared across React remounts. A busy detector is joined, not stopped.
 * Returns null when the detector never started, so the caller can score the file instead.
 */
export function beginLiveDetect(
  stream: MediaStream,
  detectUrl: string,
  onStatus?: (text: string) => void,
  onEvents?: (events: ActionServiceEvent[]) => void,
): LiveDetect {
  const base = detectUrl.replace(/\/$/, "")
  if (active && !active.closed && active.base === base) {
    active.setStatus(onStatus)
    active.setEvents(onEvents)
    active.adoptStream(stream)
    active.beginTake()
    active.handle.resume()
    return active.handle
  }

  const spans = new Map<string, LiveSpan>()
  const savedIds = new Set<string>()
  const pauses: PauseRange[] = []
  let takeAnchor = performance.now()
  let takeOrigin = 0
  let liveStartedAt: number | null = null
  let pauseBegan: number | null = null
  let arm = false
  let attached = false
  let acceptCloses = false
  let pumping = false
  let pumpGen = 0
  let pumpTimer: number | null = null
  let acceptedOnce = false
  let conflictStreak = 0
  let report = onStatus
  let reportEvents = onEvents

  function liveNow(): number {
    if (liveStartedAt == null) return 0
    return (performance.now() - liveStartedAt) / 1000
  }

  function emit() {
    reportEvents?.(eventsForTake(spans.values(), takeOrigin, liveNow(), pauses))
  }

  function noteClock(start: number | null | undefined) {
    if (liveStartedAt != null || start == null) return
    liveStartedAt = performance.now() - start * 1000
    if (takeOrigin === 0) takeOrigin = start
  }

  const video = document.createElement("video")
  video.muted = true
  video.autoplay = true
  video.playsInline = true
  video.setAttribute("playsinline", "true")
  // A video that is not in the document often never paints. Start demo draws
  // from an on-screen element; without this, Family posts empty JPEGs the
  // server still accepts.
  video.style.cssText = "position:fixed;left:0;top:0;width:160px;height:120px;opacity:0.02;pointer-events:none"
  document.body.appendChild(video)
  video.srcObject = stream
  void video.play().catch(() => undefined)
  const canvas = document.createElement("canvas")

  function unmountVideo() {
    video.srcObject = null
    video.remove()
  }

  const source = new EventSource(`${base}/api/stream`)
  source.onmessage = (message) => {
    let payload: LiveNotice & { kind?: string; warmup_sec?: number; message?: string }
    try {
      payload = JSON.parse(message.data) as typeof payload
    } catch {
      return
    }
    if (payload.kind === "live_started") {
      if (!arm) return
      if (!attached) spans.clear()
      acceptCloses = true
      liveStartedAt = performance.now()
      if (takeOrigin === 0) takeOrigin = -((performance.now() - takeAnchor) / 1000)
      const warmup = Math.round(payload.warmup_sec ?? 30)
      report?.(`Watching. The first ${warmup} seconds are warmup.`)
    } else if (payload.kind === "live_warmed_up") {
      if (!acceptCloses) return
      report?.("Detections can open now.")
    } else if (payload.kind === "live_error" && payload.message) {
      if (!acceptCloses) return
      report?.(payload.message)
    } else if ((payload.kind === "live_open" || payload.kind === "live_close") && acceptCloses) {
      const id = payload.event_id || (payload.class && payload.start != null ? `${payload.class}-${payload.start}` : "")
      if (id && savedIds.has(id)) return
      noteClock(payload.start)
      upsertLiveSpan(spans, payload, payload.kind === "live_close" ? "close" : "open")
      emit()
    }
  }

  function stopPump() {
    pumping = false
    pumpGen += 1
    if (pumpTimer != null) {
      window.clearTimeout(pumpTimer)
      pumpTimer = null
    }
  }

  async function tick(gen: number) {
    if (gen !== pumpGen || !pumping) return
    if (video.readyState >= 2 && video.videoWidth > 0 && video.videoHeight > 0) {
      const width = video.videoWidth
      const height = video.videoHeight
      if (canvas.width !== width) canvas.width = width
      if (canvas.height !== height) canvas.height = height
      canvas.getContext("2d")?.drawImage(video, 0, 0, width, height)
      const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.7))
      if (gen !== pumpGen || !pumping) return
      if (blob) {
        try {
          const res = await fetch(`${base}/api/frame`, {
            method: "POST",
            headers: { "Content-Type": "image/jpeg" },
            body: blob,
          })
          if (res.ok) {
            acceptedOnce = true
            conflictStreak = 0
          } else if (res.status === 409 && acceptedOnce) {
            conflictStreak += 1
            if (conflictStreak >= CONFLICT_STOP_AFTER) {
              stopPump()
              return
            }
          }
        } catch {
          // Keep trying until the take stops.
        }
      }
    }
    if (gen !== pumpGen || !pumping) return
    pumpTimer = window.setTimeout(() => void tick(gen), FRAME_INTERVAL_MS)
  }

  function startPump() {
    if (pumping) return
    pumping = true
    acceptedOnce = false
    conflictStreak = 0
    const gen = pumpGen
    pumpTimer = window.setTimeout(() => void tick(gen), 0)
  }

  function closePause() {
    if (pauseBegan == null) return
    pauses.push({ start: (pauseBegan - takeAnchor) / 1000, end: (performance.now() - takeAnchor) / 1000 })
    pauseBegan = null
  }

  function beginTake() {
    closePause()
    takeAnchor = performance.now()
    takeOrigin = liveNow()
    pauseBegan = null
    pauses.length = 0
    spans.clear()
    emit()
  }

  async function waitForPicture() {
    const deadline = performance.now() + 4000
    while (performance.now() < deadline) {
      if (video.videoWidth > 0 && video.readyState >= 2) return
      await new Promise((resolve) => window.setTimeout(resolve, 50))
    }
  }

  async function waitIdle() {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      const mode = await detectorMode(base)
      if (!mode || mode === "idle") return
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    }
  }

  async function postStart(): Promise<boolean> {
    try {
      await waitForPicture()
      arm = true
      let res = await fetch(`${base}/api/live/start?source=browser`, { method: "POST" })
      if (res.status === 409) {
        // The demo (or a previous take) still owns the only live session.
        // Joining it mixes two cameras into one background, and nothing opens.
        await fetch(`${base}/api/live/stop`, { method: "POST" }).catch(() => undefined)
        await waitIdle()
        res = await fetch(`${base}/api/live/start?source=browser`, { method: "POST" })
      }
      if (!res.ok) {
        arm = false
        return false
      }
      return true
    } catch {
      arm = false
      return false
    }
  }

  const started = postStart().then((ok) => {
    if (!ok) {
      stopPump()
      source.close()
      unmountVideo()
      report?.("Detector offline. This take will be scored after you stop.")
    } else {
      startPump()
    }
    return ok
  })

  report?.("Connecting to the detector.")

  function finish(): Promise<ActionServiceEvent[] | null> {
    const run = (async () => {
      stopPump()
      closePause()
      const ok = await started
      if (!ok) {
        source.close()
        unmountVideo()
        session.closed = true
        if (active === session) active = null
        return null
      }
      // Leave the detector running, the way Start demo does, so the next take
      // keeps the same background. Wait briefly for a close already in flight.
      await new Promise((resolve) => window.setTimeout(resolve, 600))
      const events = eventsForTake(spans.values(), takeOrigin, liveNow(), pauses)
      for (const id of spans.keys()) savedIds.add(id)
      spans.clear()
      reportEvents?.(events)
      return events
    })()
    return run
  }

  const handle: LiveDetect = {
    stopPump,
    pause() {
      stopPump()
      if (pauseBegan == null) pauseBegan = performance.now()
    },
    resume() {
      closePause()
      startPump()
    },
    finish,
    abort() {
      stopPump()
      spans.clear()
      emit()
    },
  }

  const session: ActiveSession = {
    base,
    closed: false,
    setStatus(next) {
      if (next) report = next
    },
    setEvents(next) {
      reportEvents = next
    },
    adoptStream(next) {
      if (video.srcObject === next) return
      video.srcObject = next
      void video.play().catch(() => undefined)
    },
    beginTake,
    handle,
  }
  active = session
  return handle
}

async function detectorMode(base: string): Promise<string | null> {
  try {
    const res = await fetch(`${base}/api/state`)
    if (!res.ok) return null
    const body = (await res.json()) as { mode?: string }
    return body.mode ?? null
  } catch {
    return null
  }
}

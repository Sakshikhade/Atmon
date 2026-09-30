import type { ActionServiceEvent } from "./detector"

const FRAME_INTERVAL_MS = 125
const CONFLICT_STOP_AFTER = 12
const STOP_WAIT_MS = 4000

type PauseRange = { start: number; end: number }

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

export type LiveDetect = {
  stopPump: () => void
  pause: () => void
  resume: () => void
  finish: () => Promise<ActionServiceEvent[] | null>
  abort: () => void
}

/**
 * Run the same live loop as the demo while a family take is recording.
 * Returns null when the detector never started, so the caller can score the file instead.
 */
export function beginLiveDetect(
  stream: MediaStream,
  detectUrl: string,
  onStatus?: (text: string) => void,
): LiveDetect {
  const base = detectUrl.replace(/\/$/, "")
  const anchor = performance.now()
  const spans = new Map<string, { class: string; start: number; end: number; score: number }>()
  const pauses: PauseRange[] = []
  let offsetSec = 0
  let pauseBegan: number | null = null
  let arm = false
  let acceptCloses = false
  let pumping = false
  let pumpGen = 0
  let pumpTimer: number | null = null
  let acceptedOnce = false
  let conflictStreak = 0
  let stoppedWait: (() => void) | null = null
  let finished: Promise<ActionServiceEvent[] | null> | null = null

  const video = document.createElement("video")
  video.muted = true
  video.playsInline = true
  video.srcObject = stream
  void video.play().catch(() => undefined)
  const canvas = document.createElement("canvas")

  const source = new EventSource(`${base}/api/stream`)
  source.onmessage = (message) => {
    let payload: {
      kind?: string
      class?: string
      start?: number
      end?: number
      score?: number
      event_id?: string
      warmup_sec?: number
      message?: string
    }
    try {
      payload = JSON.parse(message.data) as typeof payload
    } catch {
      return
    }
    if (payload.kind === "live_started") {
      if (!arm) return
      spans.clear()
      acceptCloses = true
      offsetSec = (performance.now() - anchor) / 1000
      const warmup = Math.round(payload.warmup_sec ?? 30)
      onStatus?.(`Watching. The first ${warmup} seconds are warmup.`)
    } else if (payload.kind === "live_warmed_up") {
      if (!acceptCloses) return
      onStatus?.("Detections can open now.")
    } else if (payload.kind === "live_error" && payload.message) {
      if (!acceptCloses) return
      onStatus?.(payload.message)
    } else if (
      payload.kind === "live_close" &&
      acceptCloses &&
      payload.class &&
      payload.start != null &&
      payload.end != null
    ) {
      spans.set(payload.event_id || `${payload.class}-${payload.start}`, {
        class: payload.class,
        start: payload.start,
        end: payload.end,
        score: Number(payload.score ?? 0),
      })
    } else if (payload.kind === "live_stopped" && acceptCloses) {
      stoppedWait?.()
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
    if (video.readyState >= 2) {
      const width = video.videoWidth || 640
      const height = video.videoHeight || 480
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
    pauses.push({ start: (pauseBegan - anchor) / 1000, end: (performance.now() - anchor) / 1000 })
    pauseBegan = null
  }

  async function waitIdle() {
    for (let attempt = 0; attempt < 20; attempt += 1) {
      try {
        const res = await fetch(`${base}/api/state`)
        if (!res.ok) return
        const body = (await res.json()) as { mode?: string }
        if (!body.mode || body.mode === "idle") return
      } catch {
        return
      }
      await new Promise((resolve) => window.setTimeout(resolve, 300))
    }
  }

  async function postStart(): Promise<boolean> {
    try {
      arm = true
      let res = await fetch(`${base}/api/live/start?source=browser`, { method: "POST" })
      if (res.status === 409) {
        arm = false
        acceptCloses = false
        spans.clear()
        await fetch(`${base}/api/live/stop`, { method: "POST" }).catch(() => undefined)
        await waitIdle()
        arm = true
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
      onStatus?.("Detector offline. This take will be scored after you stop.")
    }
    return ok
  })

  startPump()
  onStatus?.("Connecting to the detector.")

  function finish(): Promise<ActionServiceEvent[] | null> {
    if (finished) return finished
    finished = (async () => {
      stopPump()
      closePause()
      const ok = await started
      if (!ok) {
        source.close()
        video.srcObject = null
        return null
      }
      const stopped = new Promise<void>((resolve) => {
        const timer = window.setTimeout(resolve, STOP_WAIT_MS)
        stoppedWait = () => {
          window.clearTimeout(timer)
          resolve()
        }
      })
      await fetch(`${base}/api/live/stop`, { method: "POST" }).catch(() => undefined)
      await stopped
      source.close()
      video.srcObject = null
      return [...spans.values()].map((span) => ({
        class: span.class,
        start_sec: liveSpanToVideoSec(span.start, offsetSec, pauses),
        end_sec: liveSpanToVideoSec(span.end, offsetSec, pauses),
        score: span.score,
      }))
    })()
    return finished
  }

  return {
    stopPump,
    pause() {
      stopPump()
      if (pauseBegan == null) pauseBegan = performance.now()
    },
    resume() {
      closePause()
      if (!finished) startPump()
    },
    finish,
    abort() {
      void finish()
    },
  }
}

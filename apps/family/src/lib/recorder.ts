import { keepSegment, type TimeRange } from "./exposure"

export type AssentMark = { id: string; kind: "paused_by_child" | "resumed"; atMs: number }

export type SegmentPiece = {
  seq: number
  startMs: number
  durationMs: number
  blob: Blob
}

export type Take = {
  blob: Blob
  mime: string
  preRollMs: number
  durationMs: number
  segments: SegmentPiece[]
  obscured: boolean
  removed: TimeRange[]
  assent: AssentMark[]
}

type FaceBox = { x: number; y: number; w: number; h: number }
type FaceDetectorLike = {
  detect: (input: CanvasImageSource) => Promise<{ boundingBox: DOMRectReadOnly }[]>
}

const SEGMENT_MS = 2000
const PRE_ROLL_MS = 30000

function pickMime(): string {
  const candidates = ["video/webm;codecs=vp8,opus", "video/webm;codecs=vp8", "video/webm"]
  return candidates.find((item) => MediaRecorder.isTypeSupported(item)) ?? ""
}

function faceDetector(): FaceDetectorLike | null {
  const ctor = (window as unknown as { FaceDetector?: new () => FaceDetectorLike }).FaceDetector
  if (!ctor) return null
  try {
    return new ctor()
  } catch {
    return null
  }
}

/**
 * Keeps a 2-second segment ring of the last 30 seconds, then continues that
 * same recorder when the parent taps Record so the lead-in stays playable.
 */
export class PhoneRecorder {
  private raw: MediaStream | null = null
  private preview: MediaStream | null = null
  private recorder: MediaRecorder | null = null
  private pieces: { blob: Blob; at: number }[] = []
  private generationStarted = 0
  private takeStarted: number | null = null
  private pausedTotal = 0
  private pauseBegan: number | null = null
  private videoEl: HTMLVideoElement | null = null
  private raf = 0
  private rotateTimer = 0
  private detectTimer = 0
  private obscuring = true
  private subject: { x: number; y: number } | null = null
  private faces: FaceBox[] = []
  private detector: FaceDetectorLike | null = null
  private didBlur = false
  private mime = "video/webm"
  private removed: TimeRange[] = []
  private assent: AssentMark[] = []
  private redactUntil = 0
  private disposed = false

  bufferedMs(): number {
    if (!this.generationStarted) return 0
    return Math.min(PRE_ROLL_MS, Math.max(0, Math.round(performance.now() - this.generationStarted)))
  }

  blurred(): boolean {
    return this.obscuring && this.didBlur
  }

  async arm(): Promise<MediaStream> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error("This browser has no camera.")
    }
    this.disposed = false
    try {
      this.raw = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user" },
        audio: true,
      })
    } catch {
      this.raw = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: "user" },
        audio: false,
      })
    }
    if (this.disposed) {
      this.release()
      throw new Error("Camera stopped.")
    }
    this.detector = faceDetector()
    this.preview = this.buildPreview(this.raw)
    await this.startGeneration(this.preview)
    this.rotateTimer = window.setInterval(() => {
      if (this.disposed || this.takeStarted !== null || !this.preview) return
      if (performance.now() - this.generationStarted < 28000) return
      void this.startGeneration(this.preview)
    }, 1000)
    return this.preview
  }

  setObscuring(on: boolean) {
    this.obscuring = on
  }

  setAudio(on: boolean) {
    for (const track of this.raw?.getAudioTracks() ?? []) track.enabled = on
  }

  setSubject(x: number, y: number) {
    this.subject = { x, y }
  }

  startTake() {
    this.takeStarted = performance.now()
    this.pausedTotal = 0
    this.pauseBegan = null
    this.removed = []
    this.assent = []
    this.redactUntil = 0
    if (this.recorder?.state === "paused") this.recorder.resume()
  }

  pause() {
    if (!this.recorder || this.recorder.state !== "recording") return
    this.assent.push({ id: crypto.randomUUID(), kind: "paused_by_child", atMs: this.offsetMs() })
    this.pauseBegan = performance.now()
    this.recorder.pause()
  }

  resume() {
    if (!this.recorder || this.recorder.state !== "paused") return
    if (this.pauseBegan !== null) this.pausedTotal += performance.now() - this.pauseBegan
    this.pauseBegan = null
    this.assent.push({ id: crypto.randomUUID(), kind: "resumed", atMs: this.offsetMs() })
    this.recorder.resume()
  }

  removePart() {
    const end = this.offsetMs()
    const start = Math.max(0, end - SEGMENT_MS)
    this.removed.push({ startMs: start, endMs: Math.max(end, start + SEGMENT_MS) })
    this.redactUntil = performance.now() + SEGMENT_MS
  }

  private offsetMs(): number {
    return Math.max(0, Math.round(performance.now() - this.generationStarted))
  }

  async stopTake(): Promise<Take | null> {
    if (this.takeStarted === null) return null
    const takeStarted = this.takeStarted
    const generationStarted = this.generationStarted
    if (this.pauseBegan !== null) {
      this.pausedTotal += performance.now() - this.pauseBegan
      this.pauseBegan = null
    }
    await this.finishRecorder()
    const blobs = this.pieces.map((piece) => piece.blob)
    if (blobs.length === 0) {
      this.takeStarted = null
      if (this.preview && !this.disposed) await this.startGeneration(this.preview)
      return null
    }
    const preRollMs = Math.min(PRE_ROLL_MS, Math.max(0, Math.round(takeStarted - generationStarted)))
    const durationMs = Math.max(preRollMs + 400, Math.round(performance.now() - generationStarted - this.pausedTotal))
    const kept = this.pieces
      .map((piece, index) => ({ blob: piece.blob, startMs: index * SEGMENT_MS, header: index === 0 }))
      .filter((item) => keepSegment(item.startMs, SEGMENT_MS, this.removed, item.header))
    const segments: SegmentPiece[] = kept.map((item, index) => ({
      seq: index,
      startMs: item.startMs,
      durationMs: SEGMENT_MS,
      blob: item.blob,
    }))
    return {
      blob: new Blob(kept.map((item) => item.blob), { type: this.mime }),
      mime: this.mime,
      preRollMs,
      durationMs,
      segments,
      obscured: this.obscuring && this.didBlur,
      removed: this.removed.slice(),
      assent: this.assent.slice(),
    }
  }

  discard() {
    this.takeStarted = null
    this.pauseBegan = null
    this.pausedTotal = 0
    this.removed = []
    this.assent = []
    this.redactUntil = 0
    if (this.preview && !this.disposed) void this.startGeneration(this.preview)
  }

  dispose() {
    this.disposed = true
    this.release()
  }

  private buildPreview(raw: MediaStream): MediaStream {
    const video = document.createElement("video")
    video.srcObject = raw
    video.muted = true
    video.playsInline = true
    void video.play().catch(() => undefined)
    this.videoEl = video
    const canvas = document.createElement("canvas")
    canvas.width = 640
    canvas.height = 480
    const ctx = canvas.getContext("2d")
    const draw = () => {
      if (this.disposed || !ctx) return
      const width = video.videoWidth || 640
      const height = video.videoHeight || 480
      if (canvas.width !== width || canvas.height !== height) {
        canvas.width = width
        canvas.height = height
      }
      ctx.filter = "none"
      if (performance.now() < this.redactUntil) {
        ctx.fillStyle = "#111827"
        ctx.fillRect(0, 0, width, height)
        this.raf = requestAnimationFrame(draw)
        return
      }
      ctx.drawImage(video, 0, 0, width, height)
      if (this.obscuring) {
        const subject = this.pickSubject(width, height)
        for (const face of this.faces) {
          if (subject && face === subject) continue
          ctx.save()
          ctx.beginPath()
          ctx.rect(face.x, face.y, face.w, face.h)
          ctx.clip()
          ctx.filter = "blur(18px)"
          ctx.drawImage(video, 0, 0, width, height)
          ctx.restore()
          this.didBlur = true
        }
      }
      this.raf = requestAnimationFrame(draw)
    }
    this.raf = requestAnimationFrame(draw)
    if (this.detector) {
      this.detectTimer = window.setInterval(() => {
        void this.detector?.detect(video).then((found) => {
          this.faces = found.map((face) => ({
            x: face.boundingBox.x,
            y: face.boundingBox.y,
            w: face.boundingBox.width,
            h: face.boundingBox.height,
          }))
        }).catch(() => undefined)
      }, 500)
    }
    const stream = canvas.captureStream(15)
    for (const track of raw.getAudioTracks()) stream.addTrack(track)
    return stream
  }

  private pickSubject(width: number, height: number): FaceBox | null {
    if (this.faces.length === 0) return null
    if (!this.subject) {
      return this.faces.reduce((best, face) => (face.w * face.h > best.w * best.h ? face : best))
    }
    const px = this.subject.x * width
    const py = this.subject.y * height
    return this.faces.reduce((best, face) => {
      const bestCenter = Math.hypot(best.x + best.w / 2 - px, best.y + best.h / 2 - py)
      const center = Math.hypot(face.x + face.w / 2 - px, face.y + face.h / 2 - py)
      return center < bestCenter ? face : best
    })
  }

  private async startGeneration(stream: MediaStream) {
    await this.finishRecorder()
    if (this.disposed) return
    this.pieces = []
    this.generationStarted = performance.now()
    this.mime = pickMime() || "video/webm"
    const recorder = new MediaRecorder(stream, this.mime ? { mimeType: this.mime } : undefined)
    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) this.pieces.push({ blob: event.data, at: performance.now() })
    }
    recorder.start(SEGMENT_MS)
    this.recorder = recorder
  }

  private finishRecorder(): Promise<void> {
    const recorder = this.recorder
    this.recorder = null
    if (!recorder || recorder.state === "inactive") return Promise.resolve()
    return new Promise((resolve) => {
      let settled = false
      const done = () => {
        if (settled) return
        settled = true
        resolve()
      }
      recorder.addEventListener("stop", done, { once: true })
      try {
        recorder.stop()
      } catch {
        done()
        return
      }
      window.setTimeout(done, 2500)
    })
  }

  private release() {
    window.clearInterval(this.rotateTimer)
    window.clearInterval(this.detectTimer)
    cancelAnimationFrame(this.raf)
    void this.finishRecorder()
    this.preview?.getTracks().forEach((track) => track.stop())
    this.raw?.getTracks().forEach((track) => track.stop())
    if (this.videoEl) this.videoEl.srcObject = null
    this.preview = null
    this.raw = null
    this.videoEl = null
  }
}

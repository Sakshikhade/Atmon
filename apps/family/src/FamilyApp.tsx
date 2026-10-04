import { useEffect, useRef, useState, type ReactNode } from "react"
import {
  addNote,
  addSeenEvent,
  createGrant,
  declineAsk,
  deleteSession,
  queueOutbox,
  saveCapture,
  saveConsent,
  saveRetention,
  saveSessionDetails,
  shareOntoGrant,
  setFlag,
  updateGrant,
  verifyEvent,
} from "./lib/api"
import { detectStub, detectWithActionService, mapActionDetections } from "./lib/detector"
import { overlaps } from "./lib/exposure"
import { uploadRecording } from "./lib/cloudMedia"
import { uuidv7 } from "./lib/ids"
import { deleteVideo, loadVideo, saveVideo, sha256 } from "./lib/mediaStore"
import { PhoneRecorder } from "./lib/recorder"
import {
  CLASS_KEYS,
  CLASSES,
  LEAD_MS,
  SCOPE_LABEL,
  TAIL_MS,
  badgeWord,
  channelsLabel,
  clinicianSaid,
  confWord,
  familyStatus,
  kindLabel,
  mmss,
  roleWord,
  settingLabel,
  untilLabel,
  whenLabel,
  yourClin,
  type ClassKey,
  type ConsentKey,
  type FamilyData,
  type FamilyEvent,
  type FamilySession,
  type GrantRow,
} from "./lib/model"

type Screen =
  | "home"
  | "recording"
  | "details"
  | "processing"
  | "session"
  | "event"
  | "share"
  | "shared"
  | "log"
  | "patterns"
  | "access"
  | "settings"
  | "trust"

const HIDDEN_TABS = new Set<Screen>(["recording", "details", "processing", "share", "shared"])
const SETTINGS = ["Home", "Playground", "Shop", "Supermarket", "Transition", "Therapy", "Other"]

function Icon({ d }: { d: string }) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  )
}

const ICONS = {
  home: "M4 11 12 4l8 7v9h-5v-6H9v6H4z",
  log: "M5 7h14M5 12h14M5 17h9",
  users: "M16 19v-2a4 4 0 0 0-4-4H7a4 4 0 0 0-4 4v2M9.5 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM21 19v-2a4 4 0 0 0-3-3.87M15 4.13a4 4 0 0 1 0 7.75",
  cog: "M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6z",
  info: "M12 16v-4M12 8h.01M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z",
}

function Mark() {
  return (
    <svg width="40" height="40" viewBox="0 0 64 64" role="img" aria-label="ATMON">
      <rect width="64" height="64" rx="18" fill="#2557D6" />
      <rect x="12" y="38" width="40" height="5" rx="2.5" fill="#fff" opacity=".45" />
      <rect x="12" y="38" width="20" height="5" rx="2.5" fill="#fff" opacity=".9" />
      <circle cx="36" cy="32.5" r="9" fill="#fff" />
    </svg>
  )
}

function Dot({ classKey }: { classKey: ClassKey }) {
  return <span className="dot" style={{ background: CLASSES[classKey].color }} />
}

function mark(startMs: number, lengthMs: number, totalMs: number, color: string) {
  const total = Math.max(totalMs, 1)
  const start = Math.min(Math.max(0, startMs), total)
  const end = Math.min(total, Math.max(start, start + lengthMs))
  const left = (start / total) * 100
  const width = ((end - start) / total) * 100
  return {
    ["--mk" as string]: color,
    ["--at" as string]: `${left.toFixed(2)}%`,
    left: `${left.toFixed(2)}%`,
    width: `${width.toFixed(2)}%`,
  }
}

function Timeline({
  session,
  activeId,
  dense,
  inert,
  playhead,
  onOpen,
}: {
  session: FamilySession
  activeId?: string
  dense?: boolean
  inert?: boolean
  playhead?: number
  onOpen?: (id: string) => void
}) {
  const pin = (ms: number) => {
    const total = Math.max(session.durationMs, 1)
    const left = (Math.min(Math.max(0, ms), total) / total) * 100
    return `min(${left.toFixed(2)}%, calc(100% - 2px))`
  }
  return (
    <div className={`tl${dense ? " dense" : ""}${inert ? " inert" : ""}`}>
      <div className="track" />
      {session.preRollMs > 0 ? (
        <span className="ante" style={mark(0, session.preRollMs, session.durationMs, "var(--muted)")} />
      ) : null}
      {session.events.filter((event) => !event.mediaSuppressed).map((event) => {
        const lead = Math.max(0, event.onsetMs - LEAD_MS)
        const excluded = familyStatus(event) === "rejected"
        return (
          <span key={event.id}>
            <span className="ante" style={mark(lead, event.onsetMs - lead, session.durationMs, CLASSES[event.classKey].color)} />
            <span
              className={`ev${event.id === activeId ? " on" : ""}${excluded ? " excluded" : ""}`}
              role={inert ? undefined : "button"}
              tabIndex={inert ? undefined : 0}
              style={mark(event.onsetMs, event.durationMs, session.durationMs, CLASSES[event.classKey].color)}
              aria-label={inert ? undefined : `${CLASSES[event.classKey].name} at ${mmss(event.onsetMs / 1000)}`}
              onClick={inert ? undefined : () => onOpen?.(event.id)}
              onKeyDown={
                inert
                  ? undefined
                  : (keyEvent) => {
                      if (keyEvent.key === "Enter" || keyEvent.key === " ") {
                        keyEvent.preventDefault()
                        onOpen?.(event.id)
                      }
                    }
              }
            />
          </span>
        )
      })}
      {!inert && playhead !== undefined ? <div className="head" style={{ left: pin(playhead) }} /> : null}
      {!dense ? (
        <>
          <span className="lbl" style={{ left: 0 }}>0:00</span>
          <span className="lbl" style={{ right: 0 }}>{mmss(session.durationMs / 1000)}</span>
        </>
      ) : null}
    </div>
  )
}

function eventMeta(event: FamilyEvent, trackedTarget: boolean) {
  const status = familyStatus(event)
  const low = event.confidence === "needs_a_look" && status === "detected"
  const extra = event.source === "family" ? " (yours)" : event.channels === "audio" ? " (sound)" : ""
  const label = low ? "Needs a look" : event.source === "family" && status === "detected" ? "You added this" : badgeWord(status)
  const badgeClass = low ? "warn" : status === "confirmed" || status === "corrected" ? "ok" : status === "rejected" ? "outline" : ""
  return { low, extra, label, badgeClass, target: trackedTarget }
}

export function Intro({ signedOut, onStart }: { signedOut: boolean; onStart: () => void }) {
  return (
    <div className="intro-scrim">
      <div className="intro">
        <span className="who">For families</span>
        <div className="brandrow">
          <Mark />
          <span className="wm">atmon</span>
        </div>
        <h1>Record the moment. The log builds itself.</h1>
        <p className="lede">
          Point your phone at your child during a difficult moment. ATMON finds the behaviours, logs them with times and durations, and links every entry back to that second of video — so you can show your child's therapist exactly what happened, not just describe it from memory.
        </p>
        <div className="points">
          <div className="pt">
            <span className="num">1</span>
            <div>
              <div className="t">One tap to record</div>
              <div className="s">The record button is the first thing you see. Details wait until afterwards. Your child can always see the indicator and can pause it.</div>
            </div>
          </div>
          <div className="pt">
            <span className="num">2</span>
            <div>
              <div className="t">A log you can tap into</div>
              <div className="s">Every behaviour becomes a mark on a timeline. Tap it to jump to that moment and check whether it's right.</div>
            </div>
          </div>
          <div className="pt">
            <span className="num">3</span>
            <div>
              <div className="t">You decide what's shared</div>
              <div className="s">Send a clip to your child's therapist for as long as you choose. Nothing leaves your phone unless you send it, and you can end access any time.</div>
            </div>
          </div>
        </div>
        <div className="cta">
          <button className="btn" type="button" onClick={onStart}>Get started</button>
          {signedOut ? <span className="badge ok">Signed out</span> : <span className="foot">Your household's log, on this phone.</span>}
        </div>
      </div>
    </div>
  )
}

export function SignIn({ onSubmit }: { onSubmit: (email: string, password: string) => Promise<string | null> }) {
  const [email, setEmail] = useState("")
  const [password, setPassword] = useState("")
  const [show, setShow] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  return (
    <div className="intro-scrim">
      <div className="signin">
        <div className="brandrow">
          <Mark />
          <span className="wm">atmon</span>
        </div>
        <h1 className="h1 mt24">Sign in</h1>
        <p className="muted mt8">This phone keeps the session after you sign in once.</p>
        <label className="field mt20">
          <span>Email</span>
          <input type="email" autoComplete="username" value={email} onChange={(event) => setEmail(event.target.value)} />
        </label>
        <div className="field mt12">
          <span>Password</span>
          <span className="pw" style={{ display: "flex", alignItems: "center", border: "1px solid var(--line)", borderRadius: 10 }}>
            <input aria-label="Password" style={{ border: 0, flex: 1 }} type={show ? "text" : "password"} autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
            <button type="button" style={{ padding: "0 14px", color: "var(--primary)", fontWeight: 600 }} onClick={() => setShow((value) => !value)}>{show ? "Hide" : "Show"}</button>
          </span>
        </div>
        {error ? <p className="small mt12" style={{ color: "var(--danger)" }}>{error}</p> : null}
        <button
          className="btn mt16"
          type="button"
          disabled={busy || !email || !password}
          onClick={() => {
            setBusy(true)
            setError(null)
            void onSubmit(email, password).then((message) => {
              setBusy(false)
              if (message) setError(message)
            })
          }}
        >
          {busy ? "Signing in" : "Sign in"}
        </button>
      </div>
    </div>
  )
}

export function Splash({ onDone }: { onDone: () => void }) {
  useEffect(() => {
    const reduced = window.matchMedia("(prefers-reduced-motion: reduce)").matches
    const timer = window.setTimeout(onDone, reduced ? 900 : 2400)
    return () => window.clearTimeout(timer)
  }, [onDone])

  return (
    <div className="app-shell splash-shell">
      <div className="psplash" aria-label="ATMON">
            <svg viewBox="0 0 64 64">
              <rect className="line" x="12" y="38" width="40" height="5" rx="2.5" fill="#fff" opacity=".45" />
              <rect className="lead" x="12" y="38" width="20" height="5" rx="2.5" fill="#fff" opacity=".9" />
              <circle className="bead" cx="36" cy="32.5" r="9" fill="#fff" />
            </svg>
            <div className="word">atmon</div>
            <div className="tag">Record the moment.</div>
      </div>
    </div>
  )
}

function useCapture(active: boolean) {
  const engine = useRef<PhoneRecorder | null>(null)
  const [stream, setStream] = useState<MediaStream | null>(null)
  const [cameraError, setCameraError] = useState<string | null>(null)

  useEffect(() => {
    if (!active) return
    const recorder = new PhoneRecorder()
    engine.current = recorder
    let stop = false
    void recorder.arm().then((preview) => {
      if (stop) {
        recorder.dispose()
        return
      }
      setStream(preview)
      setCameraError(null)
    }).catch((err: unknown) => {
      if (stop) return
      const name = err instanceof DOMException ? err.name : ""
      setCameraError(name === "NotAllowedError" ? "Camera is blocked. Allow it to record." : "This browser has no camera.")
      setStream(null)
    })
    return () => {
      stop = true
      recorder.dispose()
      if (engine.current === recorder) engine.current = null
      setStream(null)
    }
  }, [active])

  return { engine, stream, cameraError }
}

function LivePreview({
  stream,
  onSubject,
}: {
  stream: MediaStream | null
  onSubject: (x: number, y: number) => void
}) {
  const ref = useRef<HTMLVideoElement>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    el.srcObject = stream
    if (stream) void el.play().catch(() => undefined)
  }, [stream])
  return (
    <video
      ref={ref}
      className="scene"
      autoPlay
      muted
      playsInline
      onClick={(event) => {
        const rect = event.currentTarget.getBoundingClientRect()
        if (rect.width === 0 || rect.height === 0) return
        onSubject((event.clientX - rect.left) / rect.width, (event.clientY - rect.top) / rect.height)
      }}
    />
  )
}

function SessionVideo({
  sessionId,
  playAt,
  playing,
  speed,
  onTime,
  onReady,
}: {
  sessionId: string
  playAt: number
  playing: boolean
  speed: number
  onTime: (ms: number, ended: boolean) => void
  onReady: (ready: boolean) => void
}) {
  const ref = useRef<HTMLVideoElement>(null)
  const [url, setUrl] = useState<string | null>(null)
  const [missing, setMissing] = useState(false)
  const onReadyRef = useRef(onReady)
  const onTimeRef = useRef(onTime)
  onReadyRef.current = onReady
  onTimeRef.current = onTime

  useEffect(() => {
    let objectUrl: string | null = null
    let cancel = false
    setMissing(false)
    setUrl(null)
    void loadVideo(sessionId).then((blob) => {
      if (cancel) return
      if (!blob) {
        setMissing(true)
        onReadyRef.current(false)
        return
      }
      objectUrl = URL.createObjectURL(blob)
      setUrl(objectUrl)
      onReadyRef.current(true)
    })
    return () => {
      cancel = true
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
  }, [sessionId])

  useEffect(() => {
    const el = ref.current
    if (!el || !url) return
    el.playbackRate = speed
    if (Math.abs(el.currentTime * 1000 - playAt) > 700) el.currentTime = playAt / 1000
    if (playing) void el.play().catch(() => undefined)
    else el.pause()
  }, [url, playing, speed, playAt])

  if (missing || !url) {
    return <div className="scene-fallback">This session has no video on this phone.</div>
  }
  return (
    <video
      ref={ref}
      className="scene"
      playsInline
      src={url}
      onTimeUpdate={(event) => onTimeRef.current(event.currentTarget.currentTime * 1000, false)}
      onEnded={(event) => onTimeRef.current(event.currentTarget.currentTime * 1000, true)}
    />
  )
}

export function FamilyApp({
  data,
  reload,
  onSignOut,
}: {
  data: FamilyData
  reload: () => Promise<void>
  onSignOut: () => void
}) {
  const [screen, setScreen] = useState<Screen>("home")
  const [tab, setTab] = useState<"home" | "log" | "access" | "settings">("home")
  const [sessionId, setSessionId] = useState(data.sessions[0]?.id ?? "")
  const [eventId, setEventId] = useState("")
  const [toast, setToast] = useState<string | null>(null)
  const [sheet, setSheet] = useState<string | null>(null)
  const [paused, setPaused] = useState(false)
  const [timer, setTimer] = useState(0)
  const [obscuring, setObscuring] = useState(true)
  const [audioOn, setAudioOn] = useState(true)
  const [setting, setSetting] = useState<string | null>(null)
  const [before, setBefore] = useState("")
  const [assent, setAssent] = useState<"yes" | "no" | null>(null)
  const [checkRun, setCheckRun] = useState(false)
  const [logView, setLogView] = useState<"sessions" | "events">("sessions")
  const [logFilter, setLogFilter] = useState<"all" | ClassKey>("all")
  const [shareFrom, setShareFrom] = useState<"event" | "session">("session")
  const [shareGrantId, setShareGrantId] = useState<string | null>(null)
  const [shareScope, setShareScope] = useState<"clip" | "session">("clip")
  const [shareExpiry, setShareExpiry] = useState<"30" | "90" | "custom">("30")
  const [expiryDate, setExpiryDate] = useState("")
  const [shareDownload, setShareDownload] = useState(false)
  const [inviteEmail, setInviteEmail] = useState("")
  const [inviteRole, setInviteRole] = useState("BCBA")
  const [playAt, setPlayAt] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [speed, setSpeed] = useState(1)
  const [noteDraft, setNoteDraft] = useState("")
  const [missedAt, setMissedAt] = useState(60)
  const [editId, setEditId] = useState<string | null>(null)
  const [endId, setEndId] = useState<string | null>(null)
  const [lastShare, setLastShare] = useState<{ what: string; who: string; until: string; download: boolean; notes: string } | null>(null)
  const [consents, setConsents] = useState(data.consents)
  const [retention, setRetention] = useState(data.retention)
  const [retentionSaved, setRetentionSaved] = useState(data.retentionSaved)
  const [freshId, setFreshId] = useState<string | null>(null)
  const [hasVideo, setHasVideo] = useState(false)
  const [saving, setSaving] = useState(false)
  const capturing = screen === "recording"
  const capture = useCapture(capturing)

  const session = data.sessions.find((item) => item.id === sessionId) ?? data.sessions[0]
  const event = session?.events.find((item) => item.id === eventId)
  const openGrants = data.grants.filter((grant) => grant.status === "active" || grant.status === "pending")
  const primary = openGrants[0] ?? null
  const clinician = yourClin(primary?.role ?? null)

  function show(message: string, ms = 2000) {
    setToast(message)
    window.setTimeout(() => setToast(null), ms)
  }

  function go(next: Screen, nextTab?: typeof tab) {
    setScreen(next)
    setSheet(null)
    if (nextTab) setTab(nextTab)
    if (next === "home" || next === "log" || next === "access" || next === "settings") setTab(next)
  }

  function openSession(id: string) {
    setSessionId(id)
    setFreshId(null)
    setCheckRun(false)
    setPlayAt(0)
    setPlaying(false)
    setHasVideo(false)
    go("session", "log")
  }

  function openEvent(id: string, sid = session?.id) {
    if (sid) setSessionId(sid)
    setEventId(id)
    const found = data.sessions.flatMap((item) => item.events).find((item) => item.id === id)
    setPlayAt(found ? Math.max(0, found.onsetMs - LEAD_MS) : 0)
    setPlaying(false)
    go("event")
  }

  useEffect(() => {
    if (screen !== "recording" || paused || saving) return
    const tick = window.setInterval(() => setTimer((value) => value + 1), 1000)
    return () => window.clearInterval(tick)
  }, [screen, paused, saving])

  useEffect(() => {
    if (screen !== "processing" || saving) return
    const timerId = window.setTimeout(() => {
      const next = freshId ?? sessionId
      if (next) openSession(next)
      else go("home")
    }, 2200)
    return () => window.clearTimeout(timerId)
  }, [screen, freshId, sessionId, saving])

  useEffect(() => {
    if (!playing || !session || hasVideo) return
    const tick = window.setInterval(() => {
      setPlayAt((value) => {
        const next = value + 250 * speed
        if (next >= session.durationMs) {
          setPlaying(false)
          return session.durationMs
        }
        return next
      })
    }, 250)
    return () => window.clearInterval(tick)
  }, [playing, speed, session, hasVideo])

  async function afterWrite(error: string | null, ok: string) {
    if (error) {
      show(error)
      return
    }
    await reload()
    show(ok)
  }

  function beginRecording() {
    if (!data.canRecord) {
      show("You don't have permission to record in this household.")
      return
    }
    setTimer(0)
    setPaused(false)
    setFreshId(null)
    go("recording")
  }

  const takeStream = useRef<MediaStream | null>(null)
  useEffect(() => {
    if (screen !== "recording") {
      takeStream.current = null
      return
    }
    const recorder = capture.engine.current
    if (!recorder || !capture.stream) return
    recorder.setObscuring(obscuring)
    recorder.setAudio(audioOn)
    if (takeStream.current === capture.stream) return
    takeStream.current = capture.stream
    recorder.startTake()
  }, [screen, capture.stream, obscuring, audioOn])

  async function finishRecording() {
    const recorder = capture.engine.current
    if (!recorder || saving) return
    setSaving(true)
    setPaused(false)
    show("Stopping — scoring this take…", 8000)
    try {
      const take = await recorder.stopTake()
      if (!take) {
        show("Nothing was recorded.")
        return
      }
      go("processing")
      const id = uuidv7()
      try {
        await saveVideo(id, take.blob)
      } catch {
        show("Couldn't keep the video on this phone.")
        go("home")
        return
      }
      const segments = []
      for (const piece of take.segments) {
        segments.push({
          seq: piece.seq,
          startMs: piece.startMs,
          durationMs: piece.durationMs,
          sha256: await sha256(piece.blob),
        })
      }
      const marker = data.tracked[0]?.key ?? "flap"
      const channels = (audioOn ? "both" : "video") as "both" | "video"
      const detectUrl = import.meta.env.VITE_DETECT_URL || "http://127.0.0.1:8010"
      let detected: ReturnType<typeof mapActionDetections> = []
      let detectorVersion = "stub"
      try {
        const scored = await detectWithActionService(take.blob, take.durationMs, detectUrl, channels)
        detected = scored.events.filter((event) => !overlaps(event.onsetMs, event.durationMs, take.removed))
        detectorVersion = scored.detectorVersion
      } catch {
        detected = detectStub(
          id,
          take.durationMs,
          take.preRollMs,
          data.tracked.map((item) => item.key),
        )
          .map((event) => ({ ...event, channels }))
          .filter((event) => !overlaps(event.onsetMs, event.durationMs, take.removed))
        show("Detector offline — used the local stand-in for this take.")
      }
      const payload = {
        sessionId: id,
        childId: data.childId,
        householdId: data.householdId,
        startedAt: new Date(Date.now() - take.durationMs).toISOString(),
        durationMs: take.durationMs,
        preRollMs: take.preRollMs,
        channels,
        obscured: take.obscured,
        detectorVersion,
        events: detected,
        segments,
        suppressed: take.removed.map((range) => ({
          id: uuidv7(),
          classKey: marker,
          startMs: range.startMs,
          endMs: range.endMs,
        })),
        assent: take.assent,
      }
      const error = await saveCapture(payload)
      if (error) {
        queueOutbox(payload)
        show("Saved on this phone. It will sync when you're online.")
      } else {
        const cloudError = await uploadRecording(id, take.blob)
        if (cloudError) show(cloudError)
      }
      setFreshId(id)
      setSessionId(id)
      setTimer(Math.round(take.durationMs / 1000))
      setSetting(null)
      setBefore("")
      setAssent(null)
      await reload().catch(() => undefined)
      go("details")
    } catch (error) {
      console.error("finishRecording failed", error)
      show("Couldn't finish this take. Try again.")
    } finally {
      setSaving(false)
    }
  }

  async function onVerify(decision: "confirm" | "reject") {
    if (!event) return
    const error = await verifyEvent(data.userId, event.id, decision, null)
    if (decision === "reject" && event.flagged) await setFlag(event.id, false, data.userId)
    if (error) {
      show(error)
      return
    }
    await reload()
    const remaining = (session?.events ?? []).filter((item) => !item.mediaSuppressed && item.id !== event.id && familyStatus(item) === "detected")
    if (checkRun && remaining.length) {
      openEvent(remaining[0].id)
      setCheckRun(true)
      show(decision === "confirm" ? "Confirmed." : "Marked as not this behaviour.")
      return
    }
    setCheckRun(false)
    show(checkRun && remaining.length === 0 ? "All checked." : decision === "confirm" ? "Confirmed." : "Marked as not this behaviour.")
    go("session")
  }

  function targetFor(key: ClassKey) {
    return data.tracked.find((item) => item.key === key)?.isTarget ?? false
  }

  function EventRow({ item, parent }: { item: FamilyEvent; parent: FamilySession }) {
    if (item.mediaSuppressed) {
      return (
        <button className="ev-item" type="button" onClick={() => openEvent(item.id, parent.id)}>
          <span className="title">This part was removed</span>
          <span className="meta">{mmss(item.onsetMs / 1000)}–{mmss((item.onsetMs + item.durationMs) / 1000)}</span>
          <span className="badge">Not kept</span>
        </button>
      )
    }
    const meta = eventMeta(item, targetFor(item.classKey))
    return (
      <button className="ev-item" type="button" onClick={() => openEvent(item.id, parent.id)}>
        <Dot classKey={item.classKey} />
        <span className={`title${familyStatus(item) === "rejected" ? " struck" : ""}`}>
          {CLASSES[item.classKey].short}
          {meta.extra}
        </span>
        <span className="meta">{mmss(item.onsetMs / 1000)}–{mmss((item.onsetMs + item.durationMs) / 1000)}</span>
        {item.flagged ? (
          <svg className="flag" viewBox="0 0 24 24" fill="currentColor" aria-label="Flagged">
            <path d="M5 3v18h2v-7h9l-2-4 2-4H7V3z" />
          </svg>
        ) : null}
        <span className={`badge ${meta.badgeClass}`}>{meta.label}</span>
        {item.clinicianJudgement ? <span className="badge ok">{roleWord(item.clinicianJudgement.role)} {item.clinicianJudgement.decision === "confirm" ? "confirmed" : item.clinicianJudgement.decision === "reject" ? "said not this" : "corrected"}</span> : null}
      </button>
    )
  }

  function SessionCard({ item }: { item: FamilySession }) {
    const live = item.events.filter((eventItem) => !eventItem.mediaSuppressed && familyStatus(eventItem) !== "rejected").length
    const unchecked = item.events.filter((eventItem) => !eventItem.mediaSuppressed && familyStatus(eventItem) === "detected").length
    const flagged = item.events.filter((eventItem) => !eventItem.mediaSuppressed && eventItem.flagged).length
    return (
      <button className="sess-card" type="button" onClick={() => openSession(item.id)}>
        <div className="between">
          <span className="t">{settingLabel(item.setting)}</span>
          <span className="meta">{whenLabel(item.startedAt)}</span>
        </div>
        <div className="between mt4">
          <span className="meta">
            {mmss(item.durationMs / 1000)} long, {live} events{unchecked ? `, ${unchecked} to check` : ""}
          </span>
          {flagged ? <span className="badge">{flagged} flagged</span> : null}
        </div>
        <Timeline session={item} dense inert />
      </button>
    )
  }

  const ask = data.asks[0]
  const toCheck = data.sessions.find((item) => item.events.some((eventItem) => !eventItem.mediaSuppressed && familyStatus(eventItem) === "detected"))
  const uncheckedCount = toCheck?.events.filter((eventItem) => !eventItem.mediaSuppressed && familyStatus(eventItem) === "detected").length ?? 0

  let body: ReactNode = null

  if (screen === "home") {
    body = (
      <>
        <h1 className="h1">{data.childName}</h1>
        <p className="muted mt4">Nothing is shared unless you share it.</p>
        <button className="home-start mt16" type="button" disabled={!data.canRecord} style={!data.canRecord ? { opacity: 0.45 } : undefined} onClick={beginRecording}>
          <span className="ring"><i /></span>
          <span>
            <span className="t">Record now</span>
            <span className="s">One tap. Details after. {data.childName} can see the indicator.</span>
          </span>
        </button>
        <p className="tiny muted mt8">
          {data.canRecord
            ? "The camera stays off until you tap Record now."
            : "You don't have permission to record in this household."}
        </p>
        {ask ? (
          <div className="card mt12">
            <div className="between">
              <span className="semi">{yourClin(ask.role).replace(/^y/, "Y")} asked for a capture</span>
              <span className="tiny muted">{ask.name}</span>
            </div>
            <p className="small mt8">{ask.what}{ask.setting ? `, ${ask.setting}` : ""}.</p>
            <div className="row mt12">
              <button className="btn sm" type="button" disabled={!data.canRecord} onClick={beginRecording}>Record one</button>
              <button className="btn sm quiet" type="button" onClick={() => { void declineAsk(ask.id).then((error) => afterWrite(error, "Dismissed. They aren't told why.")) }}>Not this week</button>
            </div>
          </div>
        ) : null}
        {toCheck && uncheckedCount ? (
          <button className="card mt12" style={{ display: "flex", alignItems: "center", gap: 12, width: "100%" }} type="button" onClick={() => {
            const next = toCheck.events.find((eventItem) => !eventItem.mediaSuppressed && familyStatus(eventItem) === "detected")
            if (!next) return
            setCheckRun(true)
            openEvent(next.id, toCheck.id)
            setCheckRun(true)
          }}>
            <span className="badge primary">{uncheckedCount}</span>
            <span style={{ flex: 1 }}>
              <span className="semi">Events to check from {settingLabel(toCheck.setting).toLowerCase()}</span>
              <span className="small muted" style={{ display: "block" }}>A quick yes or no on each. Takes about a minute.</span>
            </span>
            <span className="muted">›</span>
          </button>
        ) : null}
        <div className="between mt24 mb12">
          <h2 className="h3">Recent</h2>
          <button className="btn quiet sm" type="button" onClick={() => go("log")}>All sessions</button>
        </div>
        {data.sessions.slice(0, 2).map((item) => <SessionCard key={item.id} item={item} />)}
      </>
    )
  }

  if (screen === "recording") {
    body = (
      <div className="rec-screen">
        <div className="between">
          <span className={`badge ${saving ? "warn" : paused ? "warn" : "danger"}`}>
            {saving ? "Saving…" : paused ? "Paused" : "Recording"}
          </span>
          <span className="tabnum" style={{ fontSize: 22, fontWeight: 700 }}>{mmss(timer)}</span>
        </div>
        <div className="rec-stage">
          {capture.cameraError ? (
            <div className="scene-fallback">{capture.cameraError}</div>
          ) : capture.stream ? (
            <LivePreview stream={capture.stream} onSubject={(x, y) => capture.engine.current?.setSubject(x, y)} />
          ) : (
            <div className="scene-fallback">Turning the camera on.</div>
          )}
          <div style={{ position: "absolute", left: 12, top: 12, display: "flex", gap: 6, flexWrap: "wrap" }}>
            <span className="badge" style={{ background: "rgba(255,255,255,.16)", color: "#fff" }}>{data.childName}</span>
            <span className="badge" style={{ background: "rgba(255,255,255,.16)", color: "#fff" }}>{obscuring ? (capture.engine.current?.blurred() ? "Other faces obscured" : "Obscuring on") : "Obscuring off"}</span>
            {!audioOn ? <span className="badge" style={{ background: "rgba(255,255,255,.16)", color: "#fff" }}>Muted</span> : null}
          </div>
          <div style={{ position: "absolute", left: 12, bottom: 12, right: 12, fontSize: 13, color: "#F9FAFB", opacity: 0.85 }}>
            {saving
              ? "Scoring this take. Keep this screen open for a moment."
              : "The camera started when you tapped Record now. Tap a face to mark who this is about."}
          </div>
        </div>
        <div className="between" style={{ padding: "0 6px" }}>
          <button className="btn sm ghost" type="button" disabled={saving} onClick={() => setSheet("discard")}>Discard</button>
          <button className="big-stop" type="button" aria-label={saving ? "Saving take" : "Stop recording"} disabled={saving} onClick={() => void finishRecording()}><i /></button>
          <button className="btn sm ghost" type="button" disabled={saving} style={paused ? { background: "#fff", color: "#0F172A" } : undefined} onClick={() => {
            setPaused((value) => {
              if (value) capture.engine.current?.resume()
              else capture.engine.current?.pause()
              return !value
            })
          }}>{paused ? "Resume" : "Pause"}</button>
        </div>
        <div className="between mt16">
          <p className="tiny" style={{ opacity: 0.7 }}>If {data.childName} shows he'd rather not, stop. His response governs.</p>
          <span className="row">
            <button className="btn sm ghost" type="button" onClick={() => { capture.engine.current?.removePart(); show("That part will not be kept.") }}>Remove this part</button>
            <button className="btn sm ghost" type="button" onClick={() => setSheet("options")}>Options</button>
          </span>
        </div>
      </div>
    )
  }

  if (screen === "details") {
    body = (
      <>
        <div className="row">
          <span className="badge ok">Saved</span>
          <span className="small muted">{mmss(timer || session?.durationMs / 1000 || 0)} recorded, kept on this phone</span>
        </div>
        <h1 className="h2 mt12">Add details while it's fresh</h1>
        <p className="muted small mt4">All optional. You can add or change any of this later from the session.</p>
        <div className="mt20">
          <div className="small semi mb8">Where were you?</div>
          <div className="row wrap" style={{ gap: 6 }}>
            {SETTINGS.map((item) => (
              <button key={item} className={`chip${setting === item ? " on" : ""}`} type="button" onClick={() => setSetting(item)}>{item}</button>
            ))}
          </div>
        </div>
        <div className="mt20">
          <div className="small semi mb4">What happened just before?</div>
          <p className="tiny muted mb8">What set things off is usually the clinical question, and the recording often starts after it.</p>
          <label className="field">
            <textarea placeholder="e.g. Loud announcement over the speakers, we'd been queueing ten minutes" value={before} onChange={(input) => setBefore(input.target.value)} />
          </label>
          <button className="btn secondary sm mt8" type="button" onClick={() => show("Listening. Turned into text on this phone, nothing is sent.")}>Speak instead of typing</button>
        </div>
        <div className="mt20">
          <div className="small semi mb8">Did {data.childName} know you were recording?</div>
          <div className="grid2">
            <button className={`opt${assent === "yes" ? " on" : ""}`} type="button" onClick={() => setAssent("yes")}><span className="t">Yes</span><span className="s">In the way he understands</span></button>
            <button className={`opt${assent === "no" ? " on" : ""}`} type="button" onClick={() => setAssent("no")}><span className="t">Not really</span><span className="s">Noted, nothing else changes</span></button>
          </div>
        </div>
        <button
          className="btn mt24"
          type="button"
          onClick={() => {
            if (!sessionId) {
              go("processing")
              return
            }
            void saveSessionDetails({
              sessionId,
              setting,
              antecedentNote: before.trim() || null,
              childAware: assent === "yes" ? "yes" : assent === "no" ? "not_really" : null,
            }).then((error) => {
              if (error) show(error)
              else void reload()
              go("processing")
            })
          }}
        >
          Save details
        </button>
        <button className="btn quiet mt8" type="button" style={{ width: "100%", justifyContent: "center" }} onClick={() => go("processing")}>Skip for now</button>
      </>
    )
  }

  if (screen === "processing") {
    body = (
      <div style={{ flex: 1, display: "flex", flexDirection: "column", justifyContent: "center", textAlign: "center", padding: "60px 10px" }}>
        <div className="spinner" />
        <h2 className="h2 mt24">{saving ? "Scoring this take" : `Finding ${data.childName}'s tracked behaviours`}</h2>
        <p className="muted mt12">{saving ? "The camera is off. This usually takes under a minute." : "Running on this phone. Nothing has been uploaded. Usually under two minutes."}</p>
        <p className="tiny muted mt24">{saving ? "Keep this screen open." : "Session saved. You can close the app."}</p>
      </div>
    )
  }

  if (screen === "session" && session) {
    const live = session.events.filter((item) => !item.mediaSuppressed && familyStatus(item) !== "rejected").length
    const unchecked = session.events.filter((item) => !item.mediaSuppressed && familyStatus(item) === "detected").length
    body = (
      <>
        <button className="btn quiet" type="button" onClick={() => go("log")}>‹ Sessions</button>
        <h1 className="h2 mt8">{settingLabel(session.setting)}</h1>
        <p className="muted small mt4">{whenLabel(session.startedAt)}, {mmss(session.durationMs / 1000)} long. {channelsLabel(session.channels)}.</p>
        <div className="player mt12">
          <SessionVideo
            sessionId={session.id}
            playAt={playAt}
            playing={playing}
            speed={speed}
            onReady={setHasVideo}
            onTime={(ms, ended) => {
              setPlayAt(ms)
              if (ended || ms >= session.durationMs - 200) setPlaying(false)
            }}
          />
          <div className="ov"><span className="badge">{session.obscured ? "Other faces obscured" : "No other faces obscured"}</span></div>
          {session.events.some((item) => item.mediaSuppressed && playAt >= item.onsetMs && playAt < item.onsetMs + item.durationMs) ? (
            <div className="scene-fallback">This part was removed. The frames were not kept.</div>
          ) : null}
          <div className="tc">{mmss(playAt / 1000)} / {mmss(session.durationMs / 1000)}</div>
        </div>
        <input className="scrub mt8" type="range" min={0} max={Math.max(session.durationMs, 1)} step={250} value={Math.min(playAt, session.durationMs)} aria-label="Scrub through the recording" onChange={(input) => setPlayAt(Number(input.target.value))} />
        <div className="ctrls">
          <button className="btn" type="button" onClick={() => setPlaying((value) => !value)}>{playing ? "Pause" : "Play"}</button>
          <div className="spd" role="group" aria-label="Playback speed">
            {[1, 1.5, 2].map((value) => (
              <button key={value} type="button" className={speed === value ? "on" : ""} onClick={() => setSpeed(value)}>{value}x</button>
            ))}
          </div>
        </div>
        <div className="card flat mt12">
          <Timeline session={session} onOpen={(id) => openEvent(id)} />
          <p className="tiny muted">Tap a mark to open it. Hatched is the 30 seconds before.</p>
        </div>
        {session.antecedentNote ? (
          <div className="tintbox mt12">
            <div className="tiny semi muted">What happened before, in your words</div>
            <p className="small mt4">{session.antecedentNote}</p>
          </div>
        ) : (
          <button className="btn secondary sm mt12" type="button" onClick={() => { setBefore(""); go("details") }}>Add what happened before</button>
        )}
        <div className="between mt20 mb4">
          <h2 className="h3">{live} events</h2>
          {unchecked ? <span className="small muted">{unchecked} to check</span> : null}
        </div>
        <div className="card flat" style={{ padding: "0 14px" }}>
          {session.events.length === 0 ? (
            <p className="small muted" style={{ padding: "14px 0" }}>Nothing was marked in this take.</p>
          ) : (
            session.events.map((item) => <EventRow key={item.id} item={item} parent={session} />)
          )}
        </div>
        {unchecked ? (
          <button className="btn mt16" type="button" onClick={() => {
            const next = session.events.find((item) => !item.mediaSuppressed && familyStatus(item) === "detected")
            if (!next) return
            setCheckRun(true)
            openEvent(next.id)
            setCheckRun(true)
          }}>Check {unchecked} event{unchecked === 1 ? "" : "s"}</button>
        ) : null}
        <div className={`actions mt${unchecked ? "8" : "16"}`}>
          <button className="btn secondary" type="button" onClick={() => { setMissedAt(Math.round(session.durationMs / 2000)); setSheet("missed") }}>Add an event I saw</button>
          <button className={`btn${unchecked ? " secondary" : ""}`} type="button" onClick={() => { setShareFrom("session"); setShareScope("clip"); setShareGrantId(primary?.id ?? null); go("share") }}>Share with {clinician}</button>
        </div>
        <button className="btn quiet sm mt12" type="button" style={{ width: "100%", justifyContent: "center", color: "var(--danger)" }} onClick={() => setSheet("delete-session")}>Delete this session</button>
        <p className="tiny muted mt16">Kept on this phone. {session.obscured ? "Other faces obscured." : "No other faces were obscured."} An observation, not a clinical finding.</p>
      </>
    )
  }

  if (screen === "event" && session && event) {
    const left = session.events.filter((item) => !item.mediaSuppressed && familyStatus(item) === "detected").length
    const shownKey = event.correctedKey ?? event.classKey
    body = (
      <>
        <div className="between">
          <button className="btn quiet" type="button" onClick={() => { setCheckRun(false); go("session") }}>‹ Session</button>
          {checkRun ? (
            <span className="row">
              <span className="badge primary">{left} left</span>
              <button className="btn quiet sm" type="button" onClick={() => {
                const next = session.events.find((item) => !item.mediaSuppressed && familyStatus(item) === "detected" && item.id !== event.id)
                if (next) openEvent(next.id)
                else { setCheckRun(false); go("session") }
              }}>Skip</button>
            </span>
          ) : null}
        </div>
        <div className="row mt8">
          {event.mediaSuppressed ? <h1 className="h2">This part was removed</h1> : <><Dot classKey={shownKey} /><h1 className="h2">{CLASSES[shownKey].name}</h1></>}
        </div>
        <p className="muted small mt4">{event.mediaSuppressed ? "The frames were not kept." : `${kindLabel(shownKey, targetFor(shownKey))}.`} {mmss(event.onsetMs / 1000)}–{mmss((event.onsetMs + event.durationMs) / 1000)} in this clip.</p>
        <div className="player mt12">
          <SessionVideo
            sessionId={session.id}
            playAt={playAt}
            playing={playing}
            speed={speed}
            onReady={setHasVideo}
            onTime={(ms, ended) => {
              setPlayAt(ms)
              if (ended || ms >= session.durationMs - 200) setPlaying(false)
            }}
          />
          <div className="ov"><span className="badge">{session.obscured ? "Other faces obscured" : "No other faces obscured"}</span></div>
          {playAt < session.preRollMs ? <div className="ante-lbl">Before you tapped</div> : null}
          {session.events.some((item) => item.mediaSuppressed && playAt >= item.onsetMs && playAt < item.onsetMs + item.durationMs) ? (
            <div className="scene-fallback">This part was removed. The frames were not kept.</div>
          ) : null}
          <div className="tc">{mmss(playAt / 1000)} / {mmss(session.durationMs / 1000)}</div>
        </div>
        <input className="scrub" type="range" min={0} max={session.durationMs} step={250} value={playAt} aria-label="Scrub through the recording" onChange={(input) => setPlayAt(Number(input.target.value))} />
        <div className="ctrls">
          <button className="btn" type="button" onClick={() => setPlaying((value) => !value)}>{playing ? "Pause" : "Play"}</button>
          <div className="spd" role="group" aria-label="Playback speed">
            {[1, 1.5, 2].map((value) => (
              <button key={value} type="button" className={speed === value ? "on" : ""} onClick={() => setSpeed(value)}>{value}x</button>
            ))}
          </div>
        </div>
        <Timeline session={session} activeId={event.id} dense playhead={playAt} onOpen={(id) => openEvent(id)} />
        {event.mediaSuppressed ? (
          <p className="small mt12">Nothing from this stretch was kept on the phone, and it is not counted as a behaviour.</p>
        ) : (
          <>
        <div className="card flat mt12">
          <div className="between mb8">
            <span className="semi">Is this right?</span>
            <span className={`badge${event.confidence === "needs_a_look" ? " warn" : ""}`}>{event.source === "family" ? "You added this" : confWord(event.confidence)}</span>
          </div>
          <div className="check3">
            <button type="button" className={familyStatus(event) === "confirmed" ? "on" : ""} onClick={() => void onVerify("confirm")}>Yes</button>
            <button type="button" className={familyStatus(event) === "corrected" ? "on" : ""} onClick={() => setSheet("correct")}>Change</button>
            <button type="button" className={familyStatus(event) === "rejected" ? "on" : ""} onClick={() => void onVerify("reject")}>Not this</button>
          </div>
          <p className="tiny muted mt8">For {CLASSES[event.classKey].short.toLowerCase()}, the app is {CLASSES[event.classKey].reliability.toLowerCase()} Your answer is kept next to what it found, never over it.</p>
          {event.clinicianJudgement ? <p className="small mt8">{clinicianSaid(event.clinicianJudgement.role, event.clinicianJudgement.decision)}</p> : null}
        </div>
        <div className="actions mt12">
          <button className={`btn ${event.flagged ? "soft" : "secondary"}`} type="button" onClick={() => void setFlag(event.id, !event.flagged, data.userId).then((error) => afterWrite(error, event.flagged ? "Flag removed." : `Flagged for ${clinician}`))}>
            {event.flagged ? `Flagged for ${clinician}` : `Flag for ${clinician}`}
          </button>
          <button className="btn secondary" type="button" onClick={() => { setNoteDraft(event.note); setSheet("note") }}>{event.note ? "Edit note" : "Add note"}</button>
        </div>
        {event.note ? <div className="tintbox small mt8">{event.note}</div> : null}
        <button className="btn secondary mt12" type="button" onClick={() => { setShareFrom("event"); setShareScope("clip"); setShareGrantId(primary?.id ?? null); go("share") }}>Share this clip</button>
          </>
        )}
        <p className="tiny muted mt12" style={{ textAlign: "center" }}>{checkRun ? "Answering moves you to the next one." : "Answering takes you back to the session."}</p>
      </>
    )
  }

  if (screen === "share" && session) {
    const fromEvent = shareFrom === "event" && event
    const flagged = session.events.filter((item) => item.flagged && !item.mediaSuppressed)
    const back = fromEvent ? "event" : "session"
    body = (
      <>
        <button className="btn quiet" type="button" onClick={() => go(back as Screen)}>‹ Back</button>
        <h1 className="h2 mt8">Share with a clinician</h1>
        {openGrants.length === 0 ? (
          <div className="card mt16">
            <p className="semi">No clinician has access right now.</p>
            <p className="small muted mt4">Invite one by email. They verify their credentials before anything opens, and you set what they see.</p>
            <button className="btn mt12" type="button" onClick={() => setSheet("invite")}>Invite a clinician</button>
          </div>
        ) : (
          <>
            <p className="muted small mt4">You decide what they see and for how long. You can end it any time.</p>
            <div className="mt20">
              <div className="small semi mb8">Who</div>
              {openGrants.map((grant) => (
                <button key={grant.id} className={`opt${shareGrantId === grant.id ? " on" : ""}`} style={{ marginBottom: 8 }} type="button" onClick={() => setShareGrantId(grant.id)}>
                  <span className="t">{grant.displayName}, {roleWord(grant.role)}</span>
                  <span className="s">{grant.inviteEmail}. Access until {untilLabel(grant.expiresAt)}.</span>
                </button>
              ))}
              <button className="btn quiet sm" type="button" onClick={() => setSheet("invite")}>Invite someone new</button>
            </div>
            <div className="mt16">
              <div className="small semi mb8">What</div>
              <div className="grid2">
                {fromEvent ? (
                  <button className={`opt${shareScope === "clip" ? " on" : ""}`} type="button" onClick={() => setShareScope("clip")}>
                    <span className="t">Just this clip</span>
                    <span className="s">{CLASSES[event.classKey].short}, {mmss(Math.max(0, event.onsetMs - LEAD_MS) / 1000)} to {mmss((event.onsetMs + event.durationMs + TAIL_MS) / 1000)}</span>
                  </button>
                ) : (
                  <button className={`opt${shareScope === "clip" ? " on" : ""}`} type="button" onClick={() => setShareScope("clip")}>
                    <span className="t">Flagged clips</span>
                    <span className="s">{flagged.length} from this session, with lead-in</span>
                  </button>
                )}
                <button className={`opt${shareScope === "session" ? " on" : ""}`} type="button" onClick={() => setShareScope("session")}>
                  <span className="t">Whole session</span>
                  <span className="s">{mmss(session.durationMs / 1000)}, everything captured</span>
                </button>
              </div>
              {shareScope === "session" ? <div className="callout warn mt8"><Icon d={ICONS.info} /><span>A whole session includes everything that happened, not only the events.</span></div> : null}
            </div>
            <div className="mt16">
              <div className="small semi mb8">For how long</div>
              <div className="grid3">
                {([["30", "30 days"], ["90", "90 days"], ["custom", "Pick a date"]] as const).map(([value, label]) => (
                  <button key={value} className={`opt${shareExpiry === value ? " on" : ""}`} type="button" style={{ textAlign: "center" }} onClick={() => setShareExpiry(value)}><span className="t">{label}</span></button>
                ))}
              </div>
              {shareExpiry === "custom" ? (
                <label className="field mt8"><span>Access ends on</span><input type="date" value={expiryDate} onChange={(input) => setExpiryDate(input.target.value)} /></label>
              ) : null}
            </div>
            <div className="card flat mt16" style={{ padding: "0 16px" }}>
              <div className="toggle-row"><div className="lbl">Let them export the log<span className="s">Times, durations, your notes. No video or audio.</span></div><span className="badge ok">On</span></div>
              <div className="toggle-row">
                <div className="lbl">Let them download the video<span className="s">Off unless you turn it on.</span></div>
                <button className={`sw${shareDownload ? " on" : ""}`} type="button" role="switch" aria-checked={shareDownload} aria-label="Allow video download" onClick={() => setShareDownload((value) => !value)} />
              </div>
            </div>
            {shareDownload ? <div className="callout danger mt8"><Icon d={ICONS.info} /><span>A downloaded file is outside your control for good. Ending access later won't take it back.</span></div> : null}
            <p className="tiny muted mt12">Passing it on to anyone else is blocked. Every view and export is shown to you.</p>
            <button
              className="btn mt12"
              type="button"
              disabled={!fromEvent && shareScope === "clip" && flagged.length === 0}
              onClick={() => {
                const grant = openGrants.find((item) => item.id === shareGrantId) ?? openGrants[0]
                if (!grant) return
                const chosen = fromEvent ? [event] : shareScope === "clip" ? flagged : session.events.filter((item) => !item.mediaSuppressed && familyStatus(item) !== "rejected")
                const expires = shareExpiry === "custom" && expiryDate
                  ? new Date(`${expiryDate}T23:59:59`).toISOString()
                  : new Date(Date.now() + Number(shareExpiry) * 86400000).toISOString()
                const items = chosen.map((item) => ({
                  eventId: item.id,
                  startMs: Math.max(0, item.onsetMs - LEAD_MS),
                  endMs: item.onsetMs + item.durationMs + TAIL_MS,
                }))
                const what = shareScope === "session"
                  ? `Whole session: ${settingLabel(session.setting)}, ${mmss(session.durationMs / 1000)}`
                  : fromEvent
                    ? `1 clip: ${CLASSES[event.classKey].short}, ${mmss(Math.max(0, event.onsetMs - LEAD_MS) / 1000)} to ${mmss((event.onsetMs + event.durationMs + TAIL_MS) / 1000)}`
                    : `${flagged.length} flagged clip${flagged.length === 1 ? "" : "s"} from ${settingLabel(session.setting)}, each with its lead-in`
                void shareOntoGrant({
                  grantId: grant.id,
                  scope: shareScope === "session" ? "session" : fromEvent ? "clip" : "flagged",
                  expiresAt: expires,
                  downloadAllowed: shareDownload,
                  items,
                }).then(async (error) => {
                  if (error) {
                    show(error)
                    return
                  }
                  if (fromEvent) await setFlag(event.id, true, data.userId)
                  setLastShare({
                    what,
                    who: `${grant.displayName}, ${roleWord(grant.role)}`,
                    until: untilLabel(expires),
                    download: shareDownload,
                    notes: fromEvent && event.note ? "Your note travels with it." : session.antecedentNote ? "Your account of what happened before travels with it." : "",
                  })
                  setShareDownload(false)
                  await reload()
                  go("shared")
                })
              }}
            >
              Share {shareScope === "session" ? "session" : fromEvent ? "clip" : `${flagged.length} clip${flagged.length === 1 ? "" : "s"}`} with your {roleWord((openGrants.find((item) => item.id === shareGrantId) ?? openGrants[0]).role)}
            </button>
          </>
        )}
      </>
    )
  }

  if (screen === "shared" && lastShare) {
    body = (
      <>
        <div style={{ textAlign: "center", paddingTop: 28 }}>
          <div style={{ width: 64, height: 64, borderRadius: "50%", background: "var(--ok-soft)", color: "var(--ok)", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
            <svg viewBox="0 0 24 24" width="30" height="30" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round"><path d="M5 12l5 5L20 7" /></svg>
          </div>
          <h1 className="h2 mt16">Shared with {lastShare.who.split(",")[0] ? `your ${lastShare.who.split(",")[1]?.trim() || "clinician"}` : "your clinician"}</h1>
          <p className="muted small mt4">They'll see it the next time they sign in. You'll see when they view it.</p>
        </div>
        <div className="card mt20 small stack">
          <div className="between"><span className="muted">What</span><span style={{ textAlign: "right", maxWidth: "60%" }}>{lastShare.what}</span></div>
          <div className="between"><span className="muted">Who</span><span style={{ textAlign: "right" }}>{lastShare.who}</span></div>
          <div className="between"><span className="muted">For how long</span><span>{lastShare.until}</span></div>
          <div className="between"><span className="muted">They can</span><span style={{ textAlign: "right" }}>Watch it, export the log{lastShare.download ? ", download the video" : ""}</span></div>
          <div className="between"><span className="muted">They can't</span><span>{lastShare.download ? "Pass it on" : "Download it, pass it on"}</span></div>
          {lastShare.notes ? <p className="tiny muted">{lastShare.notes}</p> : null}
        </div>
        {lastShare.download ? <div className="callout warn mt12"><Icon d={ICONS.info} /><span>You allowed download. Ending access later won't take back a file already saved.</span></div> : null}
        <button className="btn mt20" type="button" onClick={() => go("session")}>Done</button>
        <button className="btn quiet mt8" type="button" style={{ width: "100%", justifyContent: "center" }} onClick={() => go("access")}>Manage sharing</button>
      </>
    )
  }

  if (screen === "log") {
    const rows = data.sessions.flatMap((item) => item.events.filter((eventItem) => familyStatus(eventItem) !== "rejected").map((eventItem) => ({ session: item, event: eventItem }))).filter((row) => (row.event.mediaSuppressed ? logFilter === "all" : logFilter === "all" || row.event.classKey === logFilter))
    body = (
      <>
        <div className="between">
          <h1 className="h1">Log</h1>
          <button className="btn quiet sm" type="button" onClick={() => go("patterns")}>Patterns</button>
        </div>
        <div className="segc mt12">
          <button type="button" className={logView === "sessions" ? "on" : ""} onClick={() => setLogView("sessions")}>Sessions</button>
          <button type="button" className={logView === "events" ? "on" : ""} onClick={() => setLogView("events")}>Events</button>
        </div>
        {logView === "sessions" ? (
          <div className="mt16">{data.sessions.map((item) => <SessionCard key={item.id} item={item} />)}</div>
        ) : (
          <>
            <div className="chiprow mt12">
              <button className={`chip${logFilter === "all" ? " on" : ""}`} type="button" onClick={() => setLogFilter("all")}>All</button>
              {CLASS_KEYS.map((key) => (
                <button key={key} className={`chip${logFilter === key ? " on" : ""}`} type="button" onClick={() => setLogFilter(key)}>
                  <span className="dot" style={{ background: CLASSES[key].color }} />
                  {CLASSES[key].short}
                </button>
              ))}
            </div>
            <p className="small muted mt4">{rows.length} events across {data.sessions.length} sessions</p>
            {data.sessions.map((item) => {
              const group = rows.filter((row) => row.session.id === item.id)
              if (!group.length) return null
              return (
                <div key={item.id}>
                  <div className="group-h"><span className="t">{settingLabel(item.setting)}</span><span className="s">{whenLabel(item.startedAt)}</span></div>
                  <div className="card flat" style={{ padding: "0 14px" }}>
                    {group.map((row) => <EventRow key={row.event.id} item={row.event} parent={item} />)}
                  </div>
                </div>
              )
            })}
          </>
        )}
      </>
    )
  }

  if (screen === "patterns") {
    const counts = new Map<ClassKey, number>()
    const bySetting = new Map<string, Map<ClassKey, number>>()
    for (const item of data.sessions) {
      for (const eventItem of item.events) {
        if (eventItem.mediaSuppressed || familyStatus(eventItem) === "rejected") continue
        counts.set(eventItem.classKey, (counts.get(eventItem.classKey) ?? 0) + 1)
        const bucket = bySetting.get(settingLabel(item.setting)) ?? new Map()
        bucket.set(eventItem.classKey, (bucket.get(eventItem.classKey) ?? 0) + 1)
        bySetting.set(settingLabel(item.setting), bucket)
      }
    }
    const max = Math.max(1, ...counts.values())
    const totalMs = data.sessions.reduce((sum, item) => sum + item.durationMs, 0)
    body = (
      <>
        <button className="btn quiet" type="button" onClick={() => go("log")}>‹ Log</button>
        <h1 className="h2 mt8">What was captured</h1>
        <p className="muted small mt4">The moments you recorded, not {data.childName}'s whole life. {data.sessions.length} sessions, {mmss(totalMs / 1000)} of recording.</p>
        <div className="card flat mt16">
          <h2 className="h4 mb12">Events by behaviour</h2>
          <div className="bars">
            {[...counts.entries()].sort((a, b) => b[1] - a[1]).map(([key, count]) => (
              <div className="bar" key={key}>
                <span className="lbl"><Dot classKey={key} /><span>{CLASSES[key].short}</span></span>
                <div className="trk"><div className="b" style={{ ["--mk" as string]: CLASSES[key].color, width: `${(count / max) * 100}%` }} /></div>
                <span className="n">{count}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="card flat mt12">
          <h2 className="h4 mb12">By setting</h2>
          {[...bySetting.entries()].map(([label, bucket]) => (
            <div className="between mb8" key={label}>
              <span className="small">{label}</span>
              <span className="row" style={{ gap: 4 }}>
                {[...bucket.entries()].map(([key, count]) => (
                  <span className="badge" key={key}><span className="dot" style={{ background: CLASSES[key].color, width: 8, height: 8 }} />{count}</span>
                ))}
              </span>
            </div>
          ))}
        </div>
        <div className="callout mt12"><Icon d={ICONS.info} /><span>One session per setting is too few to read a pattern from. {clinician.replace(/^y/, "Y")} can tell you when there's enough to say something.</span></div>
        <p className="tiny muted mt12">No comparisons to other children, no targets, no scores. Goals here are ones your family sets.</p>
      </>
    )
  }

  if (screen === "access") {
    body = (
      <>
        <h1 className="h1">Sharing</h1>
        <p className="muted small mt4">Everyone who can see any of {data.childName}'s record, and what they've done with it.</p>
        <div className="between mt16">
          <h2 className="h3">Clinicians</h2>
          <button className="btn quiet sm" type="button" onClick={() => setSheet("invite")}>Invite</button>
        </div>
        {data.grants.length === 0 ? <p className="small muted mt12">No one else can see this record.</p> : null}
        {data.grants.map((grant) => {
          const ended = grant.status === "ended" || grant.status === "expired"
          return (
            <div className="card mt12" key={grant.id}>
              <div className="between">
                <div>
                  <div className="semi">{grant.displayName}, {roleWord(grant.role)}</div>
                  <div className="small muted">{grant.inviteEmail}</div>
                </div>
                {ended ? <span className="badge">{grant.status === "expired" ? "Expired" : "Ended"}</span> : <span className="badge ok">{grant.status === "pending" ? "Invited" : "Active"}</span>}
              </div>
              <div className="divider" style={{ margin: "12px 0" }} />
              <div className="small stack">
                <div className="between"><span className="muted">Can see</span><span>{SCOPE_LABEL[grant.scope]}</span></div>
                <div className="between"><span className="muted">Until</span><span>{untilLabel(grant.expiresAt)}</span></div>
                <div className="between"><span className="muted">Viewed</span><span>{grant.viewed} clips</span></div>
                <div className="between"><span className="muted">Exported the log</span><span>{grant.exported} time{grant.exported === 1 ? "" : "s"}</span></div>
                <div className="between"><span className="muted">Video download</span><span>{grant.downloadAllowed ? "Allowed" : "Not allowed"}{grant.downloaded ? `, ${grant.downloaded} taken` : ""}</span></div>
              </div>
              {ended ? null : (
                <div className="actions mt12">
                  <button className="btn secondary" type="button" onClick={() => setEditId(grant.id)}>Edit access</button>
                  <button className="btn danger" type="button" onClick={() => setEndId(grant.id)}>End access</button>
                </div>
              )}
            </div>
          )
        })}
        <div className="between mt24 mb8">
          <h2 className="h3">Household</h2>
          <button className="btn quiet sm" type="button" onClick={() => setSheet("household")}>Add someone</button>
        </div>
        <div className="card flat" style={{ padding: "0 16px" }}>
          {data.members.map((member) => (
            <div className="list-item" key={member.userId}>
              <div className="grow">
                <div className="t">{member.label || data.displayName}</div>
                <div className="s">{member.role === "admin" ? "Administrator" : "Member"}. Record and view.</div>
              </div>
            </div>
          ))}
        </div>
        <p className="tiny muted mt12">Every session recorded by anyone in the household is visible to every administrator.</p>
      </>
    )
  }

  if (screen === "settings") {
    body = (
      <>
        <h1 className="h1">Settings</h1>
        <h2 className="h3 mt20 mb8">Where recordings live</h2>
        <div className="grid2">
          <button className={`opt${retention.where === "device" ? " on" : ""}`} type="button" onClick={() => void changeRetention("device", retention.days)}><span className="t">This phone only</span><span className="s">Strongest privacy. Lost if the phone is.</span></button>
          <button className={`opt${retention.where === "cloud" ? " on" : ""}`} type="button" onClick={() => void changeRetention("cloud", retention.days)}><span className="t">Backed up to cloud</span><span className="s">Survives a lost phone. Encrypted for your family only.</span></button>
        </div>
        <h2 className="h3 mt20 mb8">Keep full sessions for</h2>
        <div className="grid3">
          {[[30, "30 days"], [90, "90 days"], [365, "A year"]].map(([days, label]) => (
            <button key={days} className={`opt${retention.days === days ? " on" : ""}`} type="button" style={{ textAlign: "center" }} onClick={() => void changeRetention(retention.where, Number(days))}><span className="t">{label}</span></button>
          ))}
        </div>
        <p className="tiny muted mt8">Flagged and shared clips are kept until you delete them.</p>
        <h2 className="h3 mt20 mb4">Consent, one at a time</h2>
        <p className="tiny muted mb8">Each is separate. None is required to use the app.</p>
        <div className="card flat" style={{ padding: "0 16px" }}>
          {([
            ["backup", "Back recordings up to cloud", "Only if you chose cloud above."],
            ["recordings", "Use recordings to improve detection", "Off by default. Video would be used to train the model."],
            ["corrections", "Use your corrections to improve detection", "No video leaves the phone for this."],
            ["analytics", "Product usage analytics", "Which screens you use. Never what was detected."],
          ] as const).map(([key, title, sub]) => (
            <div className="toggle-row" key={key}>
              <div className="lbl">{title}<span className="s">{sub}</span></div>
              <button className={`sw${consents[key] ? " on" : ""}`} type="button" role="switch" aria-checked={consents[key]} aria-label={title} onClick={() => void toggleConsent(key)} />
            </div>
          ))}
        </div>
        <h2 className="h3 mt20 mb8">Behaviours tracked</h2>
        <div className="card flat" style={{ padding: "0 16px" }}>
          {(data.tracked.length ? data.tracked.map((item) => item.key) : CLASS_KEYS).map((key) => (
            <div className="list-item" key={key}>
              <Dot classKey={key} />
              <div className="grow">
                <div className="t">{CLASSES[key].name}</div>
                <div className="s">{kindLabel(key, targetFor(key))}</div>
              </div>
              <span className="badge ok">On</span>
            </div>
          ))}
        </div>
        <p className="tiny muted mt8">Self-regulating behaviours are logged as observed, never as a problem.</p>
        <div className="stack mt20">
          <button className="btn secondary" type="button" onClick={() => go("trust")}>What we never do</button>
          <button className="btn secondary" type="button" onClick={() => show("Export everything: recordings, logs, notes, links")}>Export everything</button>
          <button className="btn danger" type="button" onClick={() => setSheet("delete")}>Delete {data.childName}'s record</button>
        </div>
        <div className="divider" />
        <div className="between">
          <div>
            <div className="semi">{data.displayName}</div>
            <div className="small muted">Administrator. Signed in on this phone.</div>
          </div>
          <button className="btn sm secondary" type="button" onClick={() => setSheet("signout")}>Sign out</button>
        </div>
      </>
    )
  }

  if (screen === "trust") {
    body = (
      <>
        <button className="btn quiet" type="button" onClick={() => go("settings")}>‹ Settings</button>
        <h1 className="h2 mt8">What we never do with video of your child</h1>
        <div className="card flat mt16 small stack">
          <p><strong>Record silently.</strong> Only when you press start, with a visible indicator. No hidden mode, ever.</p>
          <p><strong>Move it without asking.</strong> It lives on your phone. Our cloud only if you choose it, encrypted for your family alone.</p>
          <p><strong>Let it spread.</strong> Your household and the clinicians you invite, for as long as you say. They can't pass it on.</p>
          <p><strong>Recognise faces or voices.</strong> No faceprint, no voiceprint, of anyone. You tell the app who each session is about.</p>
          <p><strong>Transcribe your home.</strong> We analyse how your child vocalises, not what is said.</p>
          <p><strong>Score anyone.</strong> Nothing ranks, grades or compares your child, you, or anyone else.</p>
          <p><strong>Sell data.</strong> Not the video, not anything identifiable, under any state's definition.</p>
          <p><strong>Keep undressing or toileting footage.</strong> Detected on the phone before anything leaves it. The frames are never kept, shared, or used for training.</p>
        </div>
        <p className="tiny muted mt12">You can always export everything, delete everything, and withdraw any consent, with no loss of the app.</p>
      </>
    )
  }

  async function changeRetention(where: "device" | "cloud", days: number) {
    const error = await saveRetention(data.householdId, where, days, retentionSaved)
    if (error) {
      show(error)
      return
    }
    setRetention({ where, days })
    setRetentionSaved(true)
  }

  async function toggleConsent(key: ConsentKey) {
    const next = !consents[key]
    const error = await saveConsent(data.householdId, data.userId, key, next)
    if (error) {
      show(error)
      return
    }
    setConsents({ ...consents, [key]: next })
  }

  const editing = data.grants.find((grant) => grant.id === editId) ?? null
  const ending = data.grants.find((grant) => grant.id === endId) ?? null

  const sheetNode = (
    <>
      {sheet === "discard" ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Discard this session?</h2>
            <p className="small muted mt8">Gone for good, including anything detected and the 30 seconds before. There's no undo.</p>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setSheet(null)}>Keep recording</button>
              <button className="btn" type="button" style={{ background: "var(--danger)" }} onClick={() => {
                capture.engine.current?.discard()
                setPaused(false)
                setTimer(0)
                setSheet(null)
                go("home")
              }}>Discard</button>
            </div>
          </div>
        </>
      ) : null}
      {sheet === "delete-session" && session ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Delete this session?</h2>
            <p className="small muted mt8">Removes the video on this phone and the cloud copy of this take. There is no undo.</p>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setSheet(null)}>Keep it</button>
              <button className="btn" type="button" style={{ background: "var(--danger)" }} onClick={() => {
                const id = session.id
                setSheet(null)
                void (async () => {
                  const error = await deleteSession(id)
                  if (error) {
                    show(error)
                    return
                  }
                  await deleteVideo(id).catch(() => undefined)
                  setSessionId(null)
                  setEventId(null)
                  try {
                    await reload()
                  } catch (reloadError) {
                    show(reloadError instanceof Error ? reloadError.message : "Deleted, but the list didn't refresh.")
                    go("log")
                    return
                  }
                  show("Session deleted.")
                  go("log")
                })()
              }}>Delete</button>
            </div>
          </div>
        </>
      ) : null}
      {sheet === "options" ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3 mb4">This session</h2>
            <div className="toggle-row"><div className="lbl">Obscure other people's faces<span className="s">This session only. There's no permanent off.</span></div><button className={`sw${obscuring ? " on" : ""}`} type="button" role="switch" aria-checked={obscuring} aria-label="Obscure other faces" onClick={() => setObscuring((value) => { capture.engine.current?.setObscuring(!value); return !value })} /></div>
            <div className="toggle-row"><div className="lbl">Audio<span className="s">Vocal behaviour matters clinically.</span></div><button className={`sw${audioOn ? " on" : ""}`} type="button" role="switch" aria-checked={audioOn} aria-label="Audio on" onClick={() => setAudioOn((value) => { capture.engine.current?.setAudio(!value); return !value })} /></div>
            <div className="toggle-row"><div className="lbl">Who this is about<span className="s">Everyone else in frame is a bystander.</span></div><span className="chip on">{data.childName}</span></div>
            <button className="btn mt12" type="button" onClick={() => setSheet(null)}>Back to recording</button>
          </div>
        </>
      ) : null}
      {sheet === "missed" && session ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Add an event you saw</h2>
            <p className="small muted mt4">Added as yours, and counted. It never overwrites what the app found.</p>
            <div className="between mt12"><span className="small semi">When</span><span className="tabnum small">{mmss(missedAt)}</span></div>
            <input className="scrub" type="range" min={0} max={Math.round(session.durationMs / 1000)} value={missedAt} aria-label="When it happened" onChange={(input) => setMissedAt(Number(input.target.value))} />
            <div className="small semi mt12 mb4">What was it?</div>
            {CLASS_KEYS.map((key) => (
              <button key={key} className="list-item" type="button" onClick={() => {
                void addSeenEvent({ session, classKey: key, onsetMs: missedAt * 1000 }).then((error) => {
                  setSheet(null)
                  void afterWrite(error, `Added at ${mmss(missedAt)} as yours.`)
                })
              }}>
                <Dot classKey={key} />
                <div className="grow"><div className="t">{CLASSES[key].name}</div><div className="s">{kindLabel(key, targetFor(key))}</div></div>
              </button>
            ))}
          </div>
        </>
      ) : null}
      {sheet === "correct" && event ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3 mb8">What was it?</h2>
            {CLASS_KEYS.map((key) => (
              <button key={key} className="list-item" type="button" onClick={() => {
                void verifyEvent(data.userId, event.id, "correct", key).then(async (error) => {
                  setSheet(null)
                  if (error) {
                    show(error)
                    return
                  }
                  await reload()
                  show("Changed.")
                  if (!checkRun) go("session")
                })
              }}>
                <Dot classKey={key} />
                <div className="grow"><div className="t">{CLASSES[key].name}</div><div className="s">{kindLabel(key, targetFor(key))}</div></div>
                {event.classKey === key ? <span className="badge">Current</span> : null}
              </button>
            ))}
          </div>
        </>
      ) : null}
      {sheet === "note" && event ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3 mb12">Note for this event</h2>
            <label className="field"><textarea placeholder="What the video doesn't show" value={noteDraft} onChange={(input) => setNoteDraft(input.target.value)} /></label>
            <button className="btn mt12" type="button" onClick={() => {
              void addNote(data.userId, event.id, noteDraft).then((error) => {
                setSheet(null)
                void afterWrite(error, "Note saved.")
              })
            }}>Save note</button>
          </div>
        </>
      ) : null}
      {sheet === "invite" ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Invite a clinician</h2>
            <p className="small muted mt4">They verify their credentials before anything opens. You choose what they see once they're in.</p>
            <label className="field mt12"><span>Their work email</span><input type="email" placeholder="name@clinic.org" value={inviteEmail} onChange={(input) => setInviteEmail(input.target.value)} /></label>
            <label className="field mt12">
              <span>Their role</span>
              <select value={inviteRole} onChange={(input) => setInviteRole(input.target.value)}>
                <option value="BCBA">BCBA</option>
                <option value="therapist">Therapist (RBT)</option>
                <option value="paediatrician">Paediatrician</option>
                <option value="other">Other</option>
              </select>
            </label>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setSheet(null)}>Cancel</button>
              <button className="btn" type="button" onClick={() => {
                const expires = new Date(Date.now() + 30 * 86400000).toISOString()
                void createGrant({
                  householdId: data.householdId,
                  childId: data.childId,
                  userId: data.userId,
                  email: inviteEmail.trim(),
                  role: inviteRole,
                  displayName: inviteEmail.trim().split("@")[0] || "Clinician",
                  scope: "clip",
                  expiresAt: expires,
                  downloadAllowed: false,
                  items: [],
                }).then((error) => {
                  setSheet(null)
                  void afterWrite(error, "Invite saved. They'll open it when they accept.")
                })
              }}>Send invite</button>
            </div>
          </div>
        </>
      ) : null}
      {sheet === "household" ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Add someone to the household</h2>
            <p className="small muted mt4">They sign in on their own phone. Nothing they record is hidden from the administrators.</p>
            <p className="small mt12">Sending that invite is the next account step. Nothing is added from this screen yet.</p>
            <button className="btn secondary mt16" type="button" onClick={() => setSheet(null)}>Close</button>
          </div>
        </>
      ) : null}
      {editing ? (
        <>
          <div className="scrim" onClick={() => setEditId(null)} />
          <div className="sheet">
            <h2 className="h3">What {editing.displayName}, {roleWord(editing.role)}, can see</h2>
            <div className="stack mt12">
              {(Object.entries(SCOPE_LABEL) as [GrantRow["scope"], string][]).map(([scope, label]) => (
                <button key={scope} className={`opt${editing.scope === scope ? " on" : ""}`} type="button" onClick={() => void updateGrant(editing.id, { scope }).then((error) => afterWrite(error, "Saved."))}><span className="t">{label}</span></button>
              ))}
            </div>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setEditId(null)}>Close</button>
            </div>
          </div>
        </>
      ) : null}
      {ending ? (
        <>
          <div className="scrim" onClick={() => setEndId(null)} />
          <div className="sheet">
            <h2 className="h3">End access for {ending.displayName}, {roleWord(ending.role)}?</h2>
            <p className="small muted mt8">Effective now, including anything open on their screen. The {ending.exported} log export{ending.exported === 1 ? "" : "s"} already made stay{ending.exported === 1 ? "s" : ""} with them; nothing else does.</p>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setEndId(null)}>Keep access</button>
              <button className="btn" type="button" style={{ background: "var(--danger)" }} onClick={() => {
                void updateGrant(ending.id, {
                  status: "ended",
                  ended_at: new Date().toISOString(),
                  ended_by: data.userId,
                }).then((error) => {
                  setEndId(null)
                  void afterWrite(error, "Access ended.")
                })
              }}>End access</button>
            </div>
          </div>
        </>
      ) : null}
      {sheet === "signout" ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Sign out of this phone?</h2>
            <p className="small muted mt8">Recordings stay on this phone. Nothing is deleted and nothing is shared. You'll sign in again with your email.</p>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setSheet(null)}>Stay signed in</button>
              <button className="btn" type="button" onClick={onSignOut}>Sign out</button>
            </div>
          </div>
        </>
      ) : null}
      {sheet === "delete" ? (
        <>
          <div className="scrim" onClick={() => setSheet(null)} />
          <div className="sheet">
            <h2 className="h3">Delete {data.childName}'s entire record?</h2>
            <p className="small muted mt8">Every recording, log and note, from this phone, from our cloud and its backups, and from every clinician's view. Log exports clinicians already made stay with them. You'll get written confirmation when it's done, within 30 days.</p>
            <div className="callout warn mt12"><Icon d={ICONS.info} /><span>If you only want to stop sharing, end access from Sharing instead. That keeps your record.</span></div>
            <div className="grid2 mt16">
              <button className="btn secondary" type="button" onClick={() => setSheet(null)}>Keep it</button>
              <button className="btn" type="button" style={{ background: "var(--danger)" }} onClick={() => { setSheet(null); show("Nothing was deleted. Verifiable deletion isn't connected yet.") }}>Delete everything</button>
            </div>
          </div>
        </>
      ) : null}
    </>
  )

  return (
    <div className="app-shell">
          <div className="body">{body}</div>
          {HIDDEN_TABS.has(screen) ? null : (
            <div className="tabs">
              <button type="button" className={tab === "home" ? "on" : ""} onClick={() => go("home")}><Icon d={ICONS.home} />Home</button>
              <button type="button" className={tab === "log" ? "on" : ""} onClick={() => go("log")}><Icon d={ICONS.log} />Log</button>
              <button type="button" className={tab === "access" ? "on" : ""} onClick={() => go("access")}><Icon d={ICONS.users} />Sharing</button>
              <button type="button" className={tab === "settings" ? "on" : ""} onClick={() => go("settings")}><Icon d={ICONS.cog} />Settings</button>
            </div>
          )}
          {toast ? <div className="toast">{toast}</div> : null}
          {sheetNode}
    </div>
  )
}

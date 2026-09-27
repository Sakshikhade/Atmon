import { useEffect, useMemo, useState } from "react"
import { askCapture, judgeEvent, saveAntecedent, sendFamilyNote } from "./lib/api"
import { playbackUrl } from "./lib/cloudMedia"
import {
  CLASS_KEYS,
  CLASSES,
  durWord,
  mmss,
  roleWord,
  settingLabel,
  whenLabel,
  type ClassKey,
  type ClinData,
  type ClinEvent,
  type ClinSession,
  type Decision,
} from "./lib/model"

type Screen = "home" | "queue" | "review" | "families" | "family" | "requests" | "exports" | "method" | "settings"

const LEAD_MS = 30000

export function ClinicianApp({
  data,
  reload,
  onSignOut,
}: {
  data: ClinData
  reload: () => Promise<void>
  onSignOut: () => void
}) {
  const [screen, setScreen] = useState<Screen>("home")
  const [query, setQuery] = useState("")
  const [menu, setMenu] = useState(false)
  const [toast, setToast] = useState<string | null>(null)
  const [modal, setModal] = useState<null | "request" | "correct">(null)
  const [eventId, setEventId] = useState<string | null>(null)
  const [childId, setChildId] = useState<string | null>(data.grants[0]?.childId ?? null)
  const [playAt, setPlayAt] = useState(0)
  const [playing, setPlaying] = useState(false)
  const [anteText, setAnteText] = useState("")
  const [note, setNote] = useState("")
  const [askChild, setAskChild] = useState(data.grants[0]?.childId ?? "")
  const [askWhat, setAskWhat] = useState(CLASSES.flap.name)
  const [askSetting, setAskSetting] = useState("Home")
  const [askMessage, setAskMessage] = useState("")
  const [clipUrl, setClipUrl] = useState<string | null>(null)
  const [clipNote, setClipNote] = useState<string | null>(null)

  const events = useMemo(() => data.sessions.flatMap((session) => session.events), [data.sessions])
  const waiting = events.filter((event) => event.flagged && !event.mediaSuppressed && !event.clinicianDecision)
  const reviewed = events.filter((event) => event.flagged && event.clinicianDecision)
  const event = events.find((item) => item.id === eventId) ?? null
  const session = data.sessions.find((item) => item.id === event?.sessionId) ?? null

  const sessionId = session?.id ?? null
  useEffect(() => {
    if (!sessionId) {
      setClipUrl(null)
      setClipNote(null)
      return
    }
    let cancel = false
    setClipUrl(null)
    setClipNote(null)
    void playbackUrl(sessionId).then((result) => {
      if (cancel) return
      setClipUrl(result.url)
      setClipNote(result.message)
    })
    return () => {
      cancel = true
    }
  }, [sessionId])

  function show(message: string) {
    setToast(message)
    window.setTimeout(() => setToast(null), 2200)
  }

  function openReview(next: ClinEvent) {
    setEventId(next.id)
    setPlayAt(Math.max(0, next.onsetMs - LEAD_MS))
    setPlaying(false)
    setAnteText("")
    setScreen("review")
  }

  useEffect(() => {
    if (!playing || !session) return
    const tick = window.setInterval(() => {
      setPlayAt((value) => {
        const next = value + 250
        if (next >= session.durationMs) {
          setPlaying(false)
          return session.durationMs
        }
        return next
      })
    }, 250)
    return () => window.clearInterval(tick)
  }, [playing, session])

  useEffect(() => {
    if (screen !== "review") return
    const onKey = (keyEvent: KeyboardEvent) => {
      const target = keyEvent.target as HTMLElement | null
      if (target && (target.tagName === "INPUT" || target.tagName === "TEXTAREA" || target.tagName === "SELECT")) return
      if (keyEvent.key === " " && event) {
        keyEvent.preventDefault()
        setPlaying((value) => !value)
      }
      if (keyEvent.key === "y" || keyEvent.key === "Y") {
        if (event) void judge("confirm", null)
      }
      if (keyEvent.key === "j" || keyEvent.key === "J" || keyEvent.key === "k" || keyEvent.key === "K") {
        const list = [...waiting, ...reviewed]
        const index = list.findIndex((item) => item.id === eventId)
        const step = keyEvent.key.toLowerCase() === "j" ? 1 : -1
        const next = list[index + step]
        if (next) openReview(next)
      }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  })

  async function judge(decision: Decision, classKey: ClassKey | null) {
    if (!event) return
    const error = await judgeEvent(data.userId, event.id, decision, classKey)
    if (error) {
      show(error)
      return
    }
    setModal(null)
    await reload()
    show(decision === "confirm" ? "You confirmed this." : decision === "reject" ? "You rejected this." : "You corrected this.")
  }

  const initials = data.displayName.split(" ").map((part) => part[0]).join("").slice(0, 2).toUpperCase()
  const soon = data.grants.filter((grant) => new Date(grant.expiresAt).getTime() - Date.now() < 7 * 86400000)
  const weekStart = Date.now() - 7 * 86400000
  const weekSessions = data.sessions.filter((item) => new Date(item.startedAt).getTime() >= weekStart)
  const days = Array.from({ length: 14 }, (_, index) => {
    const day = new Date()
    day.setHours(0, 0, 0, 0)
    day.setDate(day.getDate() - (13 - index))
    const count = data.sessions.filter((item) => {
      const started = new Date(item.startedAt)
      return started >= day && started < new Date(day.getTime() + 86400000)
    }).length
    return { label: day.toLocaleDateString("en-GB", { day: "numeric" }), count }
  })
  const maxDay = Math.max(1, ...days.map((day) => day.count))
  const families = data.grants.filter((grant) => `${grant.childName}`.toLowerCase().includes(query.toLowerCase()))

  let body = null
  if (screen === "home") {
    body = (
      <>
        <div className="between">
          <div>
            <h1 className="h1">Dashboard</h1>
            <p className="muted mt4">Everything below is what families have chosen to share with you.</p>
          </div>
          <div className="row">
            <button className="btn sm secondary" type="button" disabled={data.grants.length === 0} onClick={() => setModal("request")}>Ask for a capture</button>
            <button className="btn sm" type="button" onClick={() => setScreen("queue")}>Start reviewing</button>
          </div>
        </div>
        {data.grants.length === 0 ? (
          <div className="card mt24" style={{ maxWidth: 640 }}>
            <h2 className="h3">No family is sharing with this account yet</h2>
            <p className="small muted mt8">A family invites the work email you signed in with. When they share a clip, it lands in the review queue. Nothing is visible before that.</p>
          </div>
        ) : (
          <div className="dash mt24">
            <button className={`card kpi span3${waiting.length ? " alert" : ""}`} type="button" onClick={() => setScreen("queue")}><div className="l">Waiting for your review</div><div className="n">{waiting.length}</div><div className="c">{waiting.length ? "Flagged clips, newest first" : "Queue clear"}</div></button>
            <button className="card kpi span3" type="button" onClick={() => setScreen("families")}><div className="l">Families sharing with you</div><div className="n">{data.grants.length}</div><div className="c">{data.grants.filter((grant) => grant.downloadAllowed).length} allow video download</div></button>
            <div className="card kpi span3"><div className="l">Sessions this week</div><div className="n">{weekSessions.length}</div><div className="c">{weekSessions.reduce((sum, item) => sum + item.events.length, 0)} events captured</div></div>
            <button className={`card kpi span3${soon.length ? " alert" : ""}`} type="button" onClick={() => setScreen("families")}><div className="l">Access ending within a week</div><div className="n">{soon.length}</div><div className="c">{soon[0] ? `${soon[0].childName}, ${whenLabel(soon[0].expiresAt)}` : "None"}</div></button>
            <div className="card span8">
              <div className="hd"><h2>Sessions captured, last 14 days</h2><span className="small muted">{days.reduce((sum, day) => sum + day.count, 0)} total</span></div>
              <svg viewBox="0 0 560 150" width="100%" height="150" role="img" aria-label="Sessions captured per day">
                {days.map((day, index) => {
                  const height = (day.count / maxDay) * 100
                  const x = 16 + index * 38
                  return <rect key={day.label + index} x={x} y={120 - height} width="22" height={height} rx="3" fill={index === 13 ? "var(--primary)" : "var(--primary-line)"} />
                })}
              </svg>
              <p className="tiny muted mt8">Counts of what families recorded, not a measure of anything about a child.</p>
            </div>
            <div className="card span4">
              <div className="hd"><h2>Review queue</h2><button className="btn quiet sm" type="button" onClick={() => setScreen("queue")}>All {waiting.length}</button></div>
              {waiting.length === 0 ? <p className="small muted">Queue clear. New flags from families land here.</p> : waiting.slice(0, 4).map((item) => (
                <button key={item.id} className="list-item" type="button" onClick={() => openReview(item)}>
                  <Dot classKey={item.classKey} />
                  <span style={{ flex: 1 }}><span className="semi">{childName(data, item.childId)}, {CLASSES[item.classKey].short.toLowerCase()}</span><span className="tiny muted" style={{ display: "block" }}>{settingLabel(sessionSetting(data, item))}</span></span>
                  <span className="muted">›</span>
                </button>
              ))}
            </div>
            <div className="card span12">
              <div className="hd"><h2>Families</h2><button className="btn quiet sm" type="button" onClick={() => setScreen("families")}>See all</button></div>
              <div className="fam-grid">
                {families.slice(0, 4).map((grant) => {
                  const childSessions = data.sessions.filter((item) => item.childId === grant.childId)
                  const flagged = childSessions.flatMap((item) => item.events).filter((item) => item.flagged).length
                  return (
                    <button key={grant.id} className="fam-card" type="button" onClick={() => { setChildId(grant.childId); setScreen("family") }}>
                      <div className="between"><div><div className="child">{grant.childName}</div><div className="fam">Until {whenLabel(grant.expiresAt)}</div></div>{flagged ? <span className="badge primary">{flagged} flagged</span> : null}</div>
                      <div className="stats"><div><b>{childSessions.length}</b><span>sessions shared</span></div><div><b>{childSessions.reduce((sum, item) => sum + item.events.length, 0)}</b><span>events</span></div></div>
                    </button>
                  )
                })}
              </div>
            </div>
          </div>
        )}
      </>
    )
  }

  if (screen === "queue") {
    const rows = [...waiting, ...reviewed]
    body = (
      <>
        <h1 className="h1">Review queue</h1>
        <p className="muted mt4">{waiting.length} waiting, {reviewed.length} reviewed. In review: <span className="kbd">J</span> <span className="kbd">K</span> move, <span className="kbd">Y</span> confirm, <span className="kbd">Space</span> play.</p>
        {rows.length === 0 ? <p className="mt24">No flagged clips have been shared with you.</p> : (
          <table className="table mt24">
            <thead><tr><th>Child</th><th>Behaviour</th><th>When</th><th>Their note</th><th>Status</th><th></th></tr></thead>
            <tbody>
              {rows.map((item) => (
                <tr key={item.id} className="link" onClick={() => openReview(item)}>
                  <td><strong>{childName(data, item.childId)}</strong></td>
                  <td><span className="row"><Dot classKey={item.classKey} />{CLASSES[item.classKey].name}</span><div className="small muted">{durWord(item.durationMs / 1000)}</div></td>
                  <td>{whenLabel(sessionStarted(data, item))}<div className="small muted">{settingLabel(sessionSetting(data, item))}</div></td>
                  <td className="small">{item.note || "—"}</td>
                  <td>{item.clinicianDecision ? <span className="badge ok">Reviewed</span> : <span className="badge primary">Waiting</span>}</td>
                  <td><span className="btn sm secondary">Open</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </>
    )
  }

  if (screen === "families") {
    body = (
      <>
        <h1 className="h1">Families</h1>
        <p className="muted mt4">Everyone who has granted you access. Each grant is the family's, on the family's terms.</p>
        <table className="table mt24">
          <thead><tr><th>Child</th><th>Flagged</th><th>Access ends</th><th>Video download</th></tr></thead>
          <tbody>
            {families.map((grant) => {
              const flagged = data.sessions.filter((item) => item.childId === grant.childId).flatMap((item) => item.events).filter((item) => item.flagged).length
              return (
                <tr key={grant.id} className="link" onClick={() => { setChildId(grant.childId); setScreen("family") }}>
                  <td><strong>{grant.childName}</strong></td>
                  <td>{flagged ? <span className="badge primary">{flagged}</span> : <span className="muted">0</span>}</td>
                  <td>{whenLabel(grant.expiresAt)}{new Date(grant.expiresAt).getTime() - Date.now() < 7 * 86400000 ? <span className="badge warn">Soon</span> : null}</td>
                  <td className="small">{grant.downloadAllowed ? "Granted" : "Not granted"}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </>
    )
  }

  if (screen === "family" && childId) {
    const grant = data.grants.find((item) => item.childId === childId)
    const childSessions = data.sessions.filter((item) => item.childId === childId)
    body = (
      <>
        <button className="btn quiet" type="button" onClick={() => setScreen("families")}>‹ Families</button>
        <div className="between mt8">
          <h1 className="h1">{grant?.childName ?? "Family"}</h1>
          <button className="btn sm secondary" type="button" onClick={() => setModal("request")}>Ask for a capture</button>
        </div>
        {grant ? <div className="row wrap mt8"><span className="badge">{grant.scope}</span><span className="badge">Until {whenLabel(grant.expiresAt)}</span><span className="badge outline">Video download {grant.downloadAllowed ? "granted" : "not granted"}</span></div> : null}
        <h2 className="h3 mt32 mb8">Sessions shared with you</h2>
        {childSessions.length === 0 ? <p className="small muted">No session in this grant is on the log yet.</p> : childSessions.map((item) => (
          <div className="card flat mb12" key={item.id}>
            <div className="between"><div><strong>{settingLabel(item.setting)}</strong> <span className="muted small">{whenLabel(item.startedAt)}, {mmss(item.durationMs / 1000)}</span></div><span className="small muted">{item.events.filter((eventItem) => eventItem.flagged).length} flagged</span></div>
            <Timeline session={item} playAt={0} onOpen={openReview} />
          </div>
        ))}
      </>
    )
  }

  if (screen === "review" && event && session) {
    const child = childName(data, event.childId)
    const grant = data.grants.find((item) => item.childId === event.childId)
    const judged = event.clinicianDecision
    body = (
      <>
        <button className="btn quiet" type="button" onClick={() => setScreen("queue")}>‹ Review queue</button>
        <div className="review mt8">
          <div>
            <div className="between">
              <div>
                <h1 className="h2">{child}, {settingLabel(session.setting)}</h1>
                <p className="small muted mt4">{whenLabel(session.startedAt)}.{event.note ? ` Flagged note: "${event.note}"` : ""}</p>
              </div>
              <span className="badge outline">Video download {grant?.downloadAllowed ? "granted" : "not granted"}</span>
            </div>
            <div className="player mt12">
              {event.mediaSuppressed ? (
                "This part was removed. The frames were not kept."
              ) : clipUrl ? (
                <video
                  src={clipUrl}
                  controls
                  playsInline
                  onLoadedMetadata={(media) => {
                    media.currentTarget.currentTime = playAt / 1000
                  }}
                />
              ) : (
                clipNote ?? "Opening the clip…"
              )}
            </div>
            <p className="tiny muted mt8">{mmss(playAt / 1000)} / {mmss(session.durationMs / 1000)}</p>
            <Timeline session={session} activeId={event.id} playAt={playAt} onOpen={openReview} />
            <p className="tiny muted">Playback opens on the lead-in. Hatched is the 30 seconds before.</p>
            {session.antecedentNote ? <div className="tintbox mt12"><div className="tiny semi muted">The family's account of what happened before</div><p className="small mt4">{session.antecedentNote}</p></div> : null}
            <h2 className="h3 mt24 mb8">All events this session</h2>
            <div className="card flat">
              {session.events.filter((item) => !item.mediaSuppressed).map((item) => (
                <button key={item.id} className={`evrow${item.id === event.id ? " on" : ""}`} type="button" onClick={() => openReview(item)}>
                  <span className="tiny muted" style={{ width: 44 }}>{mmss(item.onsetMs / 1000)}</span>
                  <Dot classKey={item.classKey} />
                  <span style={{ flex: 1, textDecoration: item.status === "rejected" ? "line-through" : undefined }}>{CLASSES[item.classKey].name}</span>
                  <span className="badge">{item.clinicianDecision ? "Reviewed" : item.status}</span>
                </button>
              ))}
            </div>
          </div>
          <div>
            <div className="row"><Dot classKey={event.classKey} /><h2 className="h3">{CLASSES[event.classKey].name}</h2></div>
            <p className="small muted mt8">From {mmss(event.onsetMs / 1000)}, {durWord(event.durationMs / 1000)}. {event.confidence ? confLabel(event.confidence) : "Added by the family"}.</p>
            <p className="small mt8">Reliability for this behaviour: {CLASSES[event.classKey].reliability}</p>
            <div className="mt16">
              <div className="between mb8"><span className="small semi">Your judgement</span><span className="badge">{judged === "confirm" ? "You confirmed this" : judged === "correct" ? "You corrected this" : judged === "reject" ? "You rejected this" : "Not yet reviewed"}</span></div>
              <div className="judge">
                <button className={`jbtn confirm${judged === "confirm" ? " on" : ""}`} type="button" onClick={() => void judge("confirm", null)}>Confirm</button>
                <button className={`jbtn correct${judged === "correct" ? " on" : ""}`} type="button" onClick={() => setModal("correct")}>Correct</button>
                <button className={`jbtn reject${judged === "reject" ? " on" : ""}`} type="button" onClick={() => void judge("reject", null)}>Reject</button>
              </div>
              <p className="tiny muted mt8">Attributed to you and visible to the family. Their own answer is kept alongside.</p>
            </div>
            <div className="mt16">
              <div className="small semi mb8">Antecedent</div>
              {event.antecedent ? <div className="tintbox small">{event.antecedent}</div> : (
                <>
                  <p className="tiny muted mb8">No observed windows were recorded for this clip. You can still name what you think set it off.</p>
                  <label className="field"><span>Name the antecedent</span><input value={anteText} onChange={(input) => setAnteText(input.target.value)} placeholder="Demand placed after a preferred activity ended" /></label>
                  <button className="btn mt8" type="button" disabled={!anteText.trim()} onClick={() => {
                    void saveAntecedent(data.userId, event.id, anteText.trim()).then(async (error) => {
                      if (error) show(error)
                      else { await reload(); show("Antecedent saved.") }
                    })
                  }}>Save antecedent</button>
                </>
              )}
            </div>
            <div className="mt16">
              <label className="field"><span>Note to the family</span><textarea value={note} onChange={(input) => setNote(input.target.value)} placeholder="Plain language. They will read this." /></label>
              <button className="btn secondary sm mt8" type="button" disabled={!note.trim()} onClick={() => {
                void sendFamilyNote(data.userId, event.id, note.trim()).then(async (error) => {
                  if (error) show(error)
                  else { setNote(""); await reload(); show("Note sent to the family.") }
                })
              }}>Send note</button>
            </div>
            <div className="row mt16">
              <button className="btn secondary" type="button" onClick={() => show("PDF export isn't connected yet. Nothing was created.")}>Export log</button>
              <button className="btn" type="button" onClick={() => { const next = waiting.find((item) => item.id !== event.id); if (next) openReview(next); else setScreen("queue") }}>Next in queue</button>
            </div>
          </div>
        </div>
      </>
    )
  }

  if (screen === "requests") {
    body = (
      <>
        <div className="between"><h1 className="h1">Capture requests</h1><button className="btn sm" type="button" disabled={data.grants.length === 0} onClick={() => setModal("request")}>New request</button></div>
        <p className="muted mt4">Asks you've sent. Families can decline without friction, and you're not told why.</p>
        {data.requests.length === 0 ? <p className="mt24">No requests yet.</p> : (
          <table className="table mt24"><thead><tr><th>Child</th><th>Asked for</th><th>Status</th></tr></thead><tbody>
            {data.requests.map((item) => <tr key={item.id}><td><strong>{item.childName}</strong></td><td>{item.what}{item.setting ? `, ${item.setting}` : ""}</td><td><span className="badge">{item.status}</span></td></tr>)}
          </tbody></table>
        )}
      </>
    )
  }

  if (screen === "exports") {
    body = (
      <>
        <h1 className="h1">Exports</h1>
        <p className="muted mt4">Every log you've exported. Each one is also shown to the family. None contain video or audio.</p>
        <div className="card mt24" style={{ maxWidth: 640 }}>
          <p>PDF export isn't connected yet. Nothing has been created from this workspace.</p>
        </div>
      </>
    )
  }

  if (screen === "method") {
    body = (
      <>
        <h1 className="h1">How detection is validated</h1>
        <p className="muted mt4">The phone is still running the stub detector. These readings are the ones the product will keep per behaviour.</p>
        <table className="table mt24"><thead><tr><th>Behaviour</th><th>Plain reading</th></tr></thead><tbody>
          {CLASS_KEYS.map((key) => <tr key={key}><td><span className="row"><Dot classKey={key} />{CLASSES[key].name}</span></td><td className="small">{CLASSES[key].reliability}</td></tr>)}
        </tbody></table>
        <div className="callout mt24">Everything here is an observation. Nothing this product outputs is a diagnosis or a clinical finding.</div>
      </>
    )
  }

  if (screen === "settings") {
    body = (
      <>
        <h1 className="h1">Settings</h1>
        <div className="dash mt24">
          <div className="card span6">
            <div className="hd"><h2>Your profile</h2></div>
            <div className="small" style={{ display: "grid", gap: 8 }}>
              <div className="between"><span className="muted">Name</span><span>{data.displayName}</span></div>
              <div className="between"><span className="muted">Credential</span><span>{data.role ? roleWord(data.role) : "Not on file"}{data.verifiedAt ? `, verified ${whenLabel(data.verifiedAt)}` : ""}</span></div>
              <div className="between"><span className="muted">Organisation</span><span>{data.orgName ?? "Not on file"}</span></div>
            </div>
          </div>
          <div className="card span6">
            <div className="hd"><h2>What you can and can't do here</h2></div>
            <div className="small" style={{ display: "grid", gap: 8 }}>
              <p>You see only what each family grants, for as long as they grant it.</p>
              <p>You can't pass footage on. Video stays on their phone until they choose to upload it.</p>
              <p>Your judgements are attributed to you and visible to the family. Their own answers stay alongside.</p>
            </div>
          </div>
        </div>
      </>
    )
  }

  return (
    <div className="desk">
      <div className="topbar">
        <div className="title">ATMON for clinicians<small>Review what families share. Export to the record.</small></div>
        <label className="search">Find<input aria-label="Find a family" value={query} onChange={(input) => { setQuery(input.target.value); setScreen("families") }} placeholder="Find a family or child" /></label>
        <div className="user">
          <button className="userbtn" type="button" aria-expanded={menu} onClick={() => setMenu((value) => !value)}>
            <span><span className="n">{data.displayName}</span><span className="s">{data.role ? roleWord(data.role) : "Clinician"}{data.orgName ? `, ${data.orgName}` : ""}</span></span>
            <span className="avatar">{initials || "C"}</span>
          </button>
          {menu ? <div className="umenu" role="menu"><button type="button" onClick={() => { setMenu(false); setScreen("settings") }}>Settings</button><button type="button" onClick={onSignOut}>Sign out</button></div> : null}
        </div>
      </div>
      <div className="shell">
        <nav className="side">
          <Nav on={screen === "home"} onClick={() => setScreen("home")}>Dashboard</Nav>
          <div className="grp">Work</div>
          <Nav on={screen === "queue" || screen === "review"} count={waiting.length} onClick={() => setScreen("queue")}>Review queue</Nav>
          <Nav on={screen === "families" || screen === "family"} onClick={() => setScreen("families")}>Families</Nav>
          <Nav on={screen === "requests"} onClick={() => setScreen("requests")}>Capture requests</Nav>
          <div className="grp">Records</div>
          <Nav on={screen === "exports"} onClick={() => setScreen("exports")}>Exports</Nav>
          <Nav on={screen === "method"} onClick={() => setScreen("method")}>Validation and methods</Nav>
          <div className="grp">Account</div>
          <Nav on={screen === "settings"} onClick={() => setScreen("settings")}>Settings</Nav>
          <div className="foot">Each family's data is sealed from the others. One sign-in, no crossing.</div>
        </nav>
        <div className="main">{body}</div>
      </div>
      {toast ? <div className="toast">{toast}</div> : null}
      {modal === "request" ? (
        <div className="modal-scrim" onClick={() => setModal(null)}>
          <div className="modal" onClick={(eventClick) => eventClick.stopPropagation()}>
            <h2 className="h2">Ask a family for a capture</h2>
            <label className="field mt16"><span>Child</span>
              <select value={askChild} onChange={(input) => setAskChild(input.target.value)}>{data.grants.map((grant) => <option key={grant.id} value={grant.childId}>{grant.childName}</option>)}</select>
            </label>
            <label className="field mt12"><span>Behaviour</span>
              <select value={askWhat} onChange={(input) => setAskWhat(input.target.value)}>{CLASS_KEYS.map((key) => <option key={key}>{CLASSES[key].name}</option>)}<option>Anything that happens</option></select>
            </label>
            <label className="field mt12"><span>Setting</span>
              <select value={askSetting} onChange={(input) => setAskSetting(input.target.value)}>{["Home", "Mealtime", "Playground", "Shop", "Any"].map((item) => <option key={item}>{item}</option>)}</select>
            </label>
            <label className="field mt12"><span>Note to the family</span><textarea value={askMessage} onChange={(input) => setAskMessage(input.target.value)} placeholder="Short and specific. They'll see it on their home screen." /></label>
            <div className="row mt16">
              <button className="btn secondary" type="button" onClick={() => setModal(null)}>Cancel</button>
              <button className="btn" type="button" onClick={() => {
                const grant = data.grants.find((item) => item.childId === askChild) ?? data.grants[0]
                if (!grant) return
                void askCapture({ grantId: grant.id, userId: data.userId, what: askWhat, setting: askSetting, message: askMessage }).then(async (error) => {
                  if (error) show(error)
                  else { setModal(null); await reload(); show("Sent. The family can decline without saying why.") }
                })
              }}>Send request</button>
            </div>
          </div>
        </div>
      ) : null}
      {modal === "correct" && event ? (
        <div className="modal-scrim" onClick={() => setModal(null)}>
          <div className="modal" onClick={(eventClick) => eventClick.stopPropagation()}>
            <h2 className="h2">Correct the behaviour</h2>
            <p className="muted small mt8">The original detection is kept alongside your correction.</p>
            {CLASS_KEYS.map((key) => (
              <button key={key} className="list-item" type="button" onClick={() => void judge("correct", key)}>
                <Dot classKey={key} /><span style={{ flex: 1 }}>{CLASSES[key].name}</span>
                {event.classKey === key ? <span className="badge">Current</span> : null}
              </button>
            ))}
          </div>
        </div>
      ) : null}
    </div>
  )
}

function Nav({ on, count, onClick, children }: { on: boolean; count?: number; onClick: () => void; children: string }) {
  return <button className={`nav${on ? " on" : ""}`} type="button" onClick={onClick}>{children}{count ? <span className="cnt">{count}</span> : null}</button>
}

function Dot({ classKey }: { classKey: ClassKey }) {
  return <span className="dot" style={{ background: CLASSES[classKey].color }} />
}

function Timeline({ session, activeId, playAt, onOpen }: { session: ClinSession; activeId?: string; playAt: number; onOpen: (event: ClinEvent) => void }) {
  const total = Math.max(session.durationMs, 1)
  const pin = (ms: number) => `min(${((Math.min(Math.max(0, ms), total) / total) * 100).toFixed(2)}%, calc(100% - 2px))`
  return (
    <div className="tl">
      <div className="track" />
      {session.preRollMs > 0 ? <span className="ante" style={mark(0, session.preRollMs, session.durationMs, "var(--muted)")} /> : null}
      {session.events.filter((item) => !item.mediaSuppressed).map((item) => (
        <button key={item.id} className="ev" type="button" aria-label={CLASSES[item.classKey].name} style={{ ...mark(item.onsetMs, item.durationMs, session.durationMs, CLASSES[item.classKey].color), boxShadow: item.id === activeId ? "inset 0 0 0 2px var(--ink)" : undefined }} onClick={() => onOpen(item)} />
      ))}
      <div className="head" style={{ left: pin(playAt) }} />
    </div>
  )
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

function childName(data: ClinData, childId: string): string {
  return data.grants.find((grant) => grant.childId === childId)?.childName ?? "Child"
}

function sessionSetting(data: ClinData, event: ClinEvent): string | null {
  return data.sessions.find((item) => item.id === event.sessionId)?.setting ?? null
}

function sessionStarted(data: ClinData, event: ClinEvent): string {
  return data.sessions.find((item) => item.id === event.sessionId)?.startedAt ?? new Date().toISOString()
}

function confLabel(band: "confident" | "needs_a_look"): string {
  return band === "confident" ? "Confident" : "Needs a look"
}

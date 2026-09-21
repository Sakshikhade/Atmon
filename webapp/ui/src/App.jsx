import { useCallback, useEffect, useRef, useState } from 'react';
import { getJson, post } from './api';
import { useCamera } from './hooks/useCamera';
import { useOverlay } from './hooks/useOverlay';
import './styles/app.css';

function StatusLine({ text, kind }) {
  if (!text) return <div className="status" aria-live="polite" />;
  return (
    <div className={`status${kind ? ` ${kind}` : ''}`} aria-live="polite">
      {text}
    </div>
  );
}

function modeDotClass(mode) {
  if (mode === 'idle') return 'dot ok';
  if (mode === 'live' || mode === 'recording' || mode === 'detecting') return 'dot busy';
  return 'dot warn';
}

export default function App() {
  const [state, setState] = useState(null);
  const [feed, setFeed] = useState([]);
  const [gallery, setGallery] = useState(null);
  const [liveStatus, setLiveStatus] = useState({ text: '', kind: '' });
  const [recordStatus, setRecordStatus] = useState({ text: '', kind: '' });
  const [classSelect, setClassSelect] = useState('');
  const [newClassName, setNewClassName] = useState('');
  const [newSubjectName, setNewSubjectName] = useState('');
  const [showNewSubject, setShowNewSubject] = useState(false);
  const [recordingUi, setRecordingUi] = useState(false);
  const [liveUi, setLiveUi] = useState(false);

  const overlayCanvasRef = useRef(null);
  const camera = useCamera();
  const overlay = useOverlay(camera.liveVideoRef, overlayCanvasRef);
  const uploadRef = useRef(null);

  const pushFeed = useCallback((text, opts = {}) => {
    setFeed((prev) => [{ id: `${Date.now()}-${Math.random()}`, text, ...opts }, ...prev].slice(0, 80));
  }, []);

  const refreshState = useCallback(async () => {
    const s = await getJson('/api/state');
    setState(s);
    return s;
  }, []);

  const refreshGallery = useCallback(async () => {
    const byClass = await getJson('/api/events');
    setGallery(byClass);
  }, []);

  useEffect(() => {
    refreshState().catch(() => {});
    refreshGallery().catch(() => {});
    const t = setInterval(() => refreshState().catch(() => {}), 3000);
    return () => clearInterval(t);
  }, [refreshState, refreshGallery]);

  // Reconcile UI with server ingest mode (refresh / desync recovery).
  useEffect(() => {
    if (!state) return;
    const ingesting = state.mode === 'recording' || state.mode === 'detecting';
    if (!ingesting) {
      if (!liveUi && !recordingUi) return;
      setLiveUi(false);
      setRecordingUi(false);
      camera.stop();
      overlay.clear();
      overlay.resetOpen();
      return;
    }
    if (state.mode === 'detecting' && !liveUi) {
      setLiveUi(true);
      (async () => {
        try {
          await camera.open();
          await camera.attach(camera.liveVideoRef.current);
          overlay.syncSize();
          setLiveStatus({ text: 'Reconnected to live session…', kind: '' });
        } catch (err) {
          setLiveStatus({ text: String(err.message || err), kind: 'err' });
        }
      })();
    }
    if (state.mode === 'recording' && !recordingUi) {
      setRecordingUi(true);
    }
  }, [state, liveUi, recordingUi, camera, overlay]);

  useEffect(() => {
    const es = new EventSource('/api/stream');
    es.onmessage = (ev) => {
      let msg;
      try {
        msg = JSON.parse(ev.data);
      } catch {
        return;
      }
      switch (msg.kind) {
        case 'status':
          setRecordStatus({ text: msg.message, kind: '' });
          setLiveStatus({ text: msg.message, kind: '' });
          break;
        case 'record_started':
          setRecordStatus({ text: `Recording “${msg.class_name}”…`, kind: '' });
          break;
        case 'record_progress':
          setRecordStatus({ text: `Recording… ${msg.elapsed}s`, kind: '' });
          break;
        case 'record_error':
          setRecordStatus({ text: `Camera error: ${msg.message}`, kind: 'err' });
          setRecordingUi(false);
          camera.stop();
          break;
        case 'build_progress':
          setRecordStatus({ text: msg.message, kind: '' });
          break;
        case 'build_done':
          setRecordStatus({
            text: `Ready - ${(msg.classes || []).join(', ')}`,
            kind: 'ok',
          });
          setNewClassName('');
          refreshState();
          break;
        case 'build_error':
          setRecordStatus({ text: `Build failed: ${msg.message}`, kind: 'err' });
          refreshState();
          break;
        case 'pose_frame':
          if (msg.overlay) overlay.applyConfig(msg.overlay);
          overlay.draw(msg.points);
          break;
        case 'live_started':
          setLiveStatus({
            text: `Session ${msg.session_id} - watching ${msg.classes.join(', ')}`,
            kind: '',
          });
          if (msg.warmup_sec) {
            pushFeed(`Warming up ~${Math.round(msg.warmup_sec)}s before detections can open`);
          }
          if (msg.overlay) overlay.applyConfig(msg.overlay);
          break;
        case 'live_warmed_up':
          setLiveStatus({ text: 'Ready - detections can open.', kind: 'ok' });
          pushFeed('Background warm - detections can open');
          break;
        case 'live_open':
          overlay.bumpOpen(1);
          pushFeed(
            `open · start ${Number(msg.start).toFixed(1)}s · score ${Number(msg.score).toFixed(3)}`,
            { kind: 'open', className: msg.class },
          );
          break;
        case 'live_close': {
          overlay.bumpOpen(-1);
          const dur =
            msg.duration_sec != null
              ? `${Number(msg.duration_sec).toFixed(1)}s`
              : `${Number(msg.start).toFixed(1)}→${Number(msg.end).toFixed(1)}s`;
          pushFeed(
            `close · ${dur} · score ${Number(msg.score).toFixed(3)}${msg.forced ? ' · FORCED' : ''}`,
            { kind: 'close', className: msg.class },
          );
          refreshGallery();
          break;
        }
        case 'live_discarded':
          pushFeed(
            `too short · ${Number(msg.start).toFixed(1)}→${Number(msg.end).toFixed(1)}s (open row stands)`,
            { className: msg.class },
          );
          break;
        case 'live_error':
          setLiveStatus({ text: msg.message, kind: 'err' });
          setLiveUi(false);
          camera.stop();
          overlay.clear();
          overlay.resetOpen();
          refreshState();
          break;
        case 'live_stopped':
          setLiveStatus({ text: 'Stopped.', kind: '' });
          setLiveUi(false);
          camera.stop();
          overlay.clear();
          overlay.resetOpen();
          refreshState();
          refreshGallery();
          break;
        default:
          break;
      }
    };
    return () => es.close();
  }, [camera, overlay, pushFeed, refreshGallery, refreshState]);

  const idle = state?.mode === 'idle';
  const calState = state?.calibration_state || 'uncalibrated';
  const liveOk =
    idle &&
    state?.bank_built &&
    (!state?.identity_enabled || (state?.active_subject_id && state?.face_ready));
  const liveBlockReason = (() => {
    if (liveUi) return '';
    if (!state) return 'Loading…';
    if (!idle) return `Busy (${state.mode}).`;
    if (!state.bank_built) return 'Build the prototype bank first (record a reference).';
    if (state.identity_enabled) {
      if (!state.active_subject_id) return 'Select an active subject.';
      if (!state.face_ready) {
        return 'Face gallery missing for this subject — rebuild the bank (or re-record) so faces can be harvested.';
      }
    }
    return '';
  })();

  const selectedClassName = () =>
    (classSelect === '__new__' ? newClassName : classSelect).trim();

  const onLiveStart = async () => {
    let started = false;
    try {
      setLiveStatus({ text: 'Opening camera…', kind: '' });
      await camera.open();
      await post('/api/live/start?source=browser');
      started = true;
      setLiveUi(true);
      await camera.attach(camera.liveVideoRef.current);
      overlay.syncSize();
      setLiveStatus({ text: 'Starting…', kind: '' });
    } catch (err) {
      if (started) {
        try {
          await post('/api/live/stop');
        } catch {
          /* already idle / stop raced */
        }
      }
      camera.stop();
      setLiveUi(false);
      setLiveStatus({ text: String(err.message || err), kind: 'err' });
      refreshState().catch(() => {});
    }
  };

  const onLiveStop = async () => {
    try {
      await post('/api/live/stop');
    } catch (err) {
      // Still tear down UI even if server already left detecting.
      setLiveStatus({ text: String(err.message || err), kind: 'err' });
    }
    setLiveUi(false);
    camera.stop();
    overlay.clear();
    refreshState().catch(() => {});
  };

  const onRecordStart = async () => {
    const name = selectedClassName();
    if (!name) {
      setRecordStatus({ text: 'Pick or name a use case first.', kind: 'err' });
      return;
    }
    try {
      setRecordStatus({ text: 'Opening camera…', kind: '' });
      await camera.open();
      if (classSelect === '__new__') {
        await post(`/api/classes?class_name=${encodeURIComponent(name)}`);
      }
      await post(`/api/record/start?class_name=${encodeURIComponent(name)}&source=browser`);
      setRecordingUi(true);
      await camera.attach(camera.recordVideoRef.current);
      setRecordStatus({ text: 'Recording…', kind: '' });
    } catch (err) {
      camera.stop();
      setRecordingUi(false);
      setRecordStatus({ text: String(err.message || err), kind: 'err' });
    }
  };

  const onRecordStop = async (save) => {
    try {
      const data = await post(`/api/record/stop?save=${save ? 'true' : 'false'}`);
      setRecordingUi(false);
      camera.stop();
      if (!save) {
        setRecordStatus({ text: 'Cancelled.', kind: '' });
        refreshState();
        return;
      }
      if (!data.saved) {
        setRecordStatus({ text: data.warning || 'Nothing saved.', kind: 'err' });
      } else if (data.warning) {
        setRecordStatus({ text: data.warning, kind: 'err' });
      } else {
        setRecordStatus({ text: 'Saved - building bank…', kind: '' });
      }
    } catch (err) {
      setRecordStatus({ text: String(err.message || err), kind: 'err' });
    }
  };

  const onUpload = async (file) => {
    if (!file) return;
    const name = selectedClassName();
    if (!name) {
      setRecordStatus({ text: 'Pick or name a use case first.', kind: 'err' });
      return;
    }
    try {
      if (classSelect === '__new__') {
        await post(`/api/classes?class_name=${encodeURIComponent(name)}`);
      }
      const fd = new FormData();
      fd.append('file', file);
      setRecordStatus({ text: 'Uploading…', kind: '' });
      const res = await fetch(`/api/references/upload?class_name=${encodeURIComponent(name)}`, {
        method: 'POST',
        body: fd,
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || res.statusText);
      }
      setRecordStatus({ text: 'Uploaded - building bank…', kind: '' });
    } catch (err) {
      setRecordStatus({ text: String(err.message || err), kind: 'err' });
    }
  };

  const tauLabel = (() => {
    if (!state) return '…';
    if (state.tau_high == null) return 'not set';
    if (calState === 'calibrated') {
      return (
        state.tau_high.toFixed(3) +
        (state.tau_high_source ? ` · ${state.tau_high_source}` : '')
      );
    }
    return `${state.tau_high.toFixed(3)} · ${calState.toUpperCase()}`;
  })();

  const galleryGroups = (() => {
    if (!gallery) return null;
    const names = Object.keys(gallery)
      .sort()
      .filter((name) => (gallery[name] || []).some((r) => r.clip_url));
    return names.map((name) => ({
      name,
      rows: (gallery[name] || []).filter((r) => r.clip_url),
    }));
  })();

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="topbar-inner">
          <div className="brand">Action Detector</div>
          <div className="meta">
            <span className="meta-chip mode-pill">
              <span className={modeDotClass(state?.mode)} aria-hidden="true" />
              Mode <b>{state?.mode || '…'}</b>
            </span>
            <span className="meta-chip">
              Subject <b>{state?.active_subject?.display_name || '-'}</b>
            </span>
            <span className="meta-chip">
              Backbone <b>{state?.backbone || '-'}</b>
            </span>
            <span className="meta-chip" title={state?.calibration_detail || 'Detection threshold'}>
              Threshold{' '}
              <b className={calState === 'calibrated' ? '' : 'uncal'}>{tauLabel}</b>
            </span>
            {(state?.unclosed_events || 0) > 0 && (
              <span className="meta-chip" title="Events whose last row is open">
                Unclosed <b className="uncal">{state.unclosed_events}</b>
              </span>
            )}
          </div>
        </div>
      </header>

      <main>
        <section className="panel subjects" aria-labelledby="subjects-title">
          <div className="panel-head">
            <div className="panel-intro">
              <h2 id="subjects-title">Subjects</h2>
              <p className="hint">
                {state?.identity_enabled
                  ? 'Identity gate on. Live only opens when the camera matches this subject’s face.'
                  : 'Select whose face gates Live. References and face gallery are per subject.'}
              </p>
            </div>
            <div className="head-actions">
              <select
                aria-label="Active subject"
                disabled={!idle}
                value={state?.active_subject_id || ''}
                onChange={async (e) => {
                  const id = e.target.value;
                  if (!id) return;
                  try {
                    await post(`/api/subjects/${encodeURIComponent(id)}/select`);
                    await refreshState();
                  } catch (err) {
                    setLiveStatus({ text: String(err.message || err), kind: 'err' });
                  }
                }}
              >
                <option value="">Select subject…</option>
                {(state?.subjects || []).map((sub) => (
                  <option key={sub.id} value={sub.id}>
                    {sub.display_name} ({sub.n_references} refs,{' '}
                    {sub.face_ready ? 'face ok' : 'no face'})
                  </option>
                ))}
              </select>
              {showNewSubject && (
                <input
                  type="text"
                  placeholder="New name"
                  style={{ width: '8rem' }}
                  value={newSubjectName}
                  onChange={(e) => setNewSubjectName(e.target.value)}
                  aria-label="New subject name"
                />
              )}
              <button
                className="ghost"
                type="button"
                disabled={!idle}
                onClick={async () => {
                  if (!showNewSubject) {
                    setShowNewSubject(true);
                    return;
                  }
                  const name = newSubjectName.trim();
                  if (!name) {
                    setLiveStatus({ text: 'Enter a subject name.', kind: 'err' });
                    return;
                  }
                  try {
                    await post(`/api/subjects?display_name=${encodeURIComponent(name)}`);
                    setNewSubjectName('');
                    setShowNewSubject(false);
                    await refreshState();
                  } catch (err) {
                    setLiveStatus({ text: String(err.message || err), kind: 'err' });
                  }
                }}
              >
                Add
              </button>
            </div>
          </div>
        </section>

        <section className="panel stage" aria-labelledby="live-title">
          <div className="panel-head">
            <div className="panel-intro">
              <h2 id="live-title">Live</h2>
              <p className={`hint${calState === 'calibrated' ? '' : ' uncal'}`}>
                {calState === 'calibrated'
                  ? 'Detect using this device’s webcam. Needs an active subject with a face gallery and bank.'
                  : state?.calibration_detail ||
                    'No calibrated threshold. Detections are not measured performance.'}
              </p>
            </div>
            <div className="head-actions">
              <button
                className="ghost"
                type="button"
                aria-pressed={overlay.skeletonOn ? 'true' : 'false'}
                aria-label={overlay.skeletonOn ? 'Hide pose skeleton' : 'Show pose skeleton'}
                style={{ opacity: overlay.skeletonOn ? undefined : 0.55 }}
                onClick={overlay.toggleSkeleton}
              >
                Skeleton
              </button>
              <button
                className="primary"
                type="button"
                disabled={!liveOk || liveUi}
                onClick={onLiveStart}
                title={
                  liveBlockReason ||
                  (calState === 'calibrated'
                    ? undefined
                    : 'Fixed preset, not measured performance. Run scripts/calibrate.py against a labeled eval set.')
                }
              >
                {calState === 'calibrated' ? 'Start' : 'Start demo'}
              </button>
              <button className="danger" type="button" disabled={!liveUi && state?.mode !== 'detecting'} onClick={onLiveStop}>
                Stop
              </button>
            </div>
          </div>
          {!liveOk && liveBlockReason ? (
            <p className="hint uncal" role="status">
              {liveBlockReason}
            </p>
          ) : null}
          <div className="panel-body">
            <div className="stage-grid">
              <div className="stage-camera">
                <div className={`preview-slot${liveUi ? ' is-live' : ''}`}>
                  <div className="preview-placeholder">Press Start to open this device’s camera</div>
                  <video
                    ref={camera.liveVideoRef}
                    className="preview live"
                    playsInline
                    muted
                    autoPlay
                    style={{ display: liveUi ? 'block' : 'none' }}
                    onLoadedMetadata={overlay.syncSize}
                  />
                  <canvas ref={overlayCanvasRef} className="preview overlay" />
                </div>
                <StatusLine text={liveStatus.text} kind={liveStatus.kind} />
              </div>
              <div className="stage-side">
                <p className="subhead">Event feed</p>
                <div className="feed" aria-live="polite">
                  {feed.length === 0 ? (
                    <div className="feed-empty">Waiting for detections…</div>
                  ) : (
                    feed.map((row) => (
                      <div
                        key={row.id}
                        className={
                          row.kind === 'open'
                            ? 'ev-open'
                            : row.kind === 'close'
                              ? 'ev-close'
                              : undefined
                        }
                      >
                        {row.className ? (
                          <>
                            <span className="ev-class">{row.className}</span>
                            {` · ${row.text}`}
                          </>
                        ) : (
                          row.text
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>
        </section>

        <div className="support-grid">
          <section className="panel" aria-labelledby="refs-title">
            <div className="panel-head">
              <div className="panel-intro">
                <h2 id="refs-title">References</h2>
                <p className="hint">Teach a class with a clean 2-4 s clip.</p>
              </div>
              <div className="head-actions">
                <button
                  className="ghost"
                  type="button"
                  title="Rebuild prototype bank"
                  disabled={!idle || (state?.identity_enabled && !state?.active_subject_id)}
                  onClick={async () => {
                    try {
                      setRecordStatus({ text: 'Rebuilding prototype bank…', kind: '' });
                      await post('/api/prototypes/rebuild');
                    } catch (err) {
                      setRecordStatus({ text: String(err.message || err), kind: 'err' });
                    }
                  }}
                >
                  Rebuild
                </button>
              </div>
            </div>
            <div className="panel-body">
              <p className="subhead">Classes</p>
              <div className="classes">
                {!state?.classes?.length ? (
                  <p className="empty">No classes yet - record or upload below.</p>
                ) : (
                  state.classes.map((c) => (
                    <div className="row" key={c.name}>
                      <div>
                        <div className="row-name">{c.name}</div>
                        <div className="row-sub">
                          {c.n_references} reference{c.n_references === 1 ? '' : 's'}
                        </div>
                      </div>
                      <span className={`tag ${c.ready ? 'ready' : 'notready'}`}>
                        {c.ready ? 'Ready' : 'Needs rebuild'}
                      </span>
                    </div>
                  ))
                )}
              </div>

              <div className="divider" role="presentation" />

              <p className="subhead">Add reference</p>
              <div className="controls" style={{ marginBottom: '0.7rem' }}>
                <label className="sr-only" htmlFor="classSelect">
                  Use case
                </label>
                <select
                  id="classSelect"
                  aria-label="Use case"
                  value={classSelect}
                  onChange={(e) => setClassSelect(e.target.value)}
                >
                  <option value="">Select use case…</option>
                  {(state?.classes || []).map((c) => (
                    <option key={c.name} value={c.name}>
                      {c.name}
                    </option>
                  ))}
                  <option value="__new__">New use case…</option>
                </select>
                {classSelect === '__new__' && (
                  <>
                    <label className="sr-only" htmlFor="classNameInput">
                      New use case name
                    </label>
                    <input
                      id="classNameInput"
                      type="text"
                      placeholder="new_name"
                      autoComplete="off"
                      value={newClassName}
                      onChange={(e) => setNewClassName(e.target.value)}
                      aria-label="New use case name"
                    />
                  </>
                )}
              </div>
              <div className="controls" style={{ marginBottom: '0.7rem' }}>
                <button
                  className="primary"
                  type="button"
                  disabled={
                    !idle ||
                    recordingUi ||
                    (state?.identity_enabled && !state?.active_subject_id)
                  }
                  onClick={onRecordStart}
                >
                  Record
                </button>
                <button type="button" disabled={!recordingUi} onClick={() => onRecordStop(true)}>
                  Stop &amp; save
                </button>
                <button
                  className="ghost"
                  type="button"
                  disabled={!recordingUi}
                  onClick={() => onRecordStop(false)}
                >
                  Cancel
                </button>
                <label className="file-btn">
                  Upload
                  <input
                    ref={uploadRef}
                    type="file"
                    accept="video/*"
                    disabled={
                      !idle || (state?.identity_enabled && !state?.active_subject_id)
                    }
                    onChange={(e) => {
                      const file = e.target.files?.[0];
                      e.target.value = '';
                      onUpload(file);
                    }}
                  />
                </label>
              </div>
              <video
                ref={camera.recordVideoRef}
                className="preview record"
                playsInline
                muted
                autoPlay
                style={{ display: recordingUi ? 'block' : 'none' }}
              />
              <StatusLine text={recordStatus.text} kind={recordStatus.kind} />
            </div>
          </section>

          <section className="panel" aria-labelledby="gallery-title">
            <div className="panel-head">
              <div className="panel-intro">
                <h2 id="gallery-title">Captures</h2>
                <p className="hint">Clips saved from live detections.</p>
              </div>
              <div className="head-actions">
                <button className="ghost" type="button" onClick={() => refreshGallery()}>
                  Refresh
                </button>
                <button
                  className="ghost"
                  type="button"
                  onClick={async () => {
                    if (!confirm('Delete all saved detection clips on disk?')) return;
                    try {
                      const data = await post('/api/clips/delete_all');
                      setLiveStatus({
                        text: `Removed ${data.removed || 0} clip file(s).`,
                        kind: 'ok',
                      });
                      refreshGallery();
                    } catch (err) {
                      setLiveStatus({ text: String(err.message || err), kind: 'err' });
                    }
                  }}
                >
                  Delete all
                </button>
              </div>
            </div>
            <div className="panel-body">
              <div id="gallery">
                {!galleryGroups ? null : galleryGroups.length === 0 ? (
                  <p className="empty">No captures yet - run Live above.</p>
                ) : (
                  galleryGroups.map((g) => (
                    <div className="gallery-class" key={g.name}>
                      <h3>
                        {g.name} <span className="pill">{g.rows.length}</span>
                      </h3>
                      <div className="clips">
                        {g.rows.map((r) => (
                          <div className="clip-card" key={r.event_id}>
                            <video controls preload="none" src={r.clip_url} />
                            <div className="clip-meta">
                              {r.duration_sec != null ? `${r.duration_sec.toFixed(1)}s` : ''}
                              {' · score '}
                              {r.score != null ? r.score.toFixed(2) : '?'}
                              <br />
                              {r.start_utc
                                ? new Date(r.start_utc).toLocaleString()
                                : r.source_id}
                            </div>
                            <div className="clip-actions">
                              <button
                                type="button"
                                className="ghost"
                                onClick={async () => {
                                  if (!confirm('Delete this clip file?')) return;
                                  try {
                                    await post(
                                      `/api/clip/${encodeURIComponent(r.event_id)}/delete`,
                                    );
                                    await refreshGallery();
                                  } catch (err) {
                                    alert(String(err.message || err));
                                    await refreshGallery();
                                  }
                                }}
                              >
                                Delete
                              </button>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}

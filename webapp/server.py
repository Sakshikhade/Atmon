#!/usr/bin/env python3
"""Demo web UI for the few-shot action detector (my_approach).

Not part of the spec. Wraps the existing pipeline (src/encoder.py,
src/prototypes.py, src/live.py, src/event_log.py) behind a small FastAPI app so
the three demo steps can happen from a browser instead of the CLI:

  1. record a reference clip for a class -> automatically rebuild the
     prototype bank (spec 6), and pose/hand templates if those streams are
     enabled, in the background, report when detection is ready
  2. start a live test -> run the real live detector (spec 10) against either
     the browser webcam (default for the web UI; frames POSTed as JPEGs) or
     the server's OpenCV camera -- same streams and calibrated background
     scale as scripts/detect_live.py -- and stream open/close events to the
     page as they happen
  3. browse saved clips (spec 9.6), grouped by class, playable in the browser

Calibration is intentionally simplified for the demo: instead of Phase 4's
labeled-eval-set sweep (scripts/calibrate.py), a fixed sigma threshold is used
when nothing has been calibrated. It is held IN MEMORY for the session and
never written to cache/calibration.json -- persisting it turned "we have no
threshold" into a file that every later reader, require_tau_high included,
treated as an answer. Every surface that shows it says UNCALIBRATED.

Run from my_approach/:
    python webapp/server.py
"""

import json
import os
import queue
import secrets
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.clip_writer import build_store, clips_enabled, write_frames, FrameRetentionBuffer
from src.config import add_class_to_config, calibration_status, load_config
from src.encoder import build_encoder
from src.event_log import (
    SOURCE_LIVE,
    EventLogWriter,
    live_session_id,
    new_run_id,
    read_events,
    unclosed_events,
    utc_now,
)
from src.live import (
    CameraStream,
    LiveDetector,
    load_live_pose_extractor,
    pose_required_for_scoring,
)
from src.pose import POSE_EDGES, landmarks_to_overlay
from src.prototypes import bank_path, build_bank, save_bank
from src.subjects import (
    bind_subject,
    count_reference_clips,
    create_subject,
    delete_subject,
    has_face_gallery,
    list_subjects,
    load_meta,
    migrate_legacy_references,
    save_meta,
    subject_references_dir,
)
from src.face_id import FaceIdEncoder, harvest_subject_faces, identity_enabled, load_gallery

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")

# Uncalibrated demo default. The real threshold comes from scripts/calibrate.py
# (spec 8); this exists only so the "live test" step works before that has
# ever been run. cache/calibration.json's existing value (if any) always wins.
DEMO_TAU_HIGH = 1.5

#: Message kinds that may be dropped when a subscriber falls behind. Only the
#: high-rate cosmetic ones -- never anything that records a detection.
_LOSSY_KINDS = {"pose_frame"}
POSE_QUEUE_MAX = 4


# --------------------------------------------------------------------------
# Shared state
# --------------------------------------------------------------------------

class AppState:
    def __init__(self):
        self.lock = threading.RLock()
        self.mode = "idle"  # idle | recording | building | detecting
        self.encoder = None
        self.encoder_backbone = None

        # recording
        self.record_thread = None
        self.record_stop = threading.Event()
        self.record_class = None
        self.record_frames = []  # (t, rgb frame)

        # live detection
        self.live_thread = None
        self.live_stop = threading.Event()
        self.live_session_id = None
        self.active_subject_id = None

        # browser webcam ingest (None when using server OpenCV camera)
        self.ingest_stream = None

        # broadcast
        self.subscribers = []  # list of queue.Queue, one per connected browser tab

    def publish(self, kind, payload):
        msg = json.dumps({"kind": kind, **payload})
        with self.lock:
            subs = list(self.subscribers)
        drop_if_backed_up = kind in _LOSSY_KINDS
        for q in subs:
            # pose_frame arrives ~8x/second. A tab that stops draining its queue
            # (backgrounded, throttled, network-stalled) would otherwise grow it
            # without bound. Drop the newest instead of blocking the capture
            # loop -- the same latest-wins choice CameraStream makes. Real
            # events (live_open / live_close) are never dropped.
            if drop_if_backed_up and q.qsize() > POSE_QUEUE_MAX:
                continue
            q.put(msg)


STATE = AppState()
app = FastAPI(title="Few-shot action detector -- demo")


@app.on_event("startup")
def restore_active_subject():
    """Pick a subject on boot so Start demo is not stuck until a manual select.

    Prefer a subject that already has a face gallery when identity is on.
    """
    try:
        c = cfg()
        subjects = list_subjects(c)
        if not subjects:
            return
        preferred = None
        if identity_enabled(c):
            preferred = next(
                (s for s in subjects if has_face_gallery(c, s["id"])),
                None,
            )
        chosen = preferred or subjects[0]
        with STATE.lock:
            if STATE.active_subject_id is None:
                STATE.active_subject_id = chosen["id"]
    except Exception:  # noqa: BLE001
        pass


# Optional shared secret. Unset (the default) the app is loopback-only and
# unauthenticated, exactly as before. Set, every mutating request must carry it.
APP_TOKEN = os.environ.get("APP_TOKEN", "").strip()

#: /api/frame is the webcam ingest -- ~8 POSTs/second from the page. Requiring a
#: header on it would mean putting the token in client-side JS, where it is not
#: a secret. It carries no destructive power (it feeds an in-memory frame slot
#: that only an already-running session reads), so it is exempt.
_UNPROTECTED_POSTS = {"/api/frame"}


@app.middleware("http")
async def require_token(request, call_next):
    """Gate mutating requests on APP_TOKEN when one is configured.

    GETs stay open: the page, /api/state and the SSE stream have to work for an
    unauthenticated browser to be able to present a login-less demo at all.
    What this protects is the routes that delete clips or rewrite config.yaml.
    """
    if APP_TOKEN and request.method not in ("GET", "HEAD", "OPTIONS") \
            and request.url.path not in _UNPROTECTED_POSTS:
        sent = request.headers.get("authorization", "")
        prefix = "bearer "
        if not (sent[:len(prefix)].lower() == prefix
                and secrets.compare_digest(sent[len(prefix):], APP_TOKEN)):
            from fastapi.responses import JSONResponse

            return JSONResponse(
                {"detail": "missing or invalid bearer token"}, status_code=401)
    return await call_next(request)


def cfg():
    return load_config(CONFIG_PATH)


def _ensure_subjects(c):
    """Migrate legacy refs once; return current subject list."""
    migrate_legacy_references(c, verbose=False)
    return list_subjects(c)


def bind_active_subject(c, subject_id=None, require=False):
    """Bind cfg to a subject. Pass require=True when the caller needs one."""
    _ensure_subjects(c)
    sid = subject_id if subject_id is not None else STATE.active_subject_id
    if require and not sid:
        raise HTTPException(
            409,
            "select an active subject first (identity gate is on)",
        )
    if sid:
        try:
            bind_subject(c, sid)
        except FileNotFoundError as exc:
            raise HTTPException(404, str(exc)) from exc
    return c, sid


def get_encoder(c):
    """Load-once encoder, shared by prototype building and live detection."""
    backbone = c.get("backbone", "vjepa")
    with STATE.lock:
        if STATE.encoder is None or STATE.encoder_backbone != backbone:
            STATE.publish("status", {"message": "loading %s encoder (first time downloads weights)..." % backbone})
            STATE.encoder = build_encoder(c, batch_size=4)
            STATE.encoder_backbone = backbone
        return STATE.encoder


def _unclosed_count(c):
    """How many events in the log never got a closing row (spec 9.2)."""
    try:
        return len(unclosed_events(c.path(c["event_log"]["path"])))
    except Exception:  # noqa: BLE001
        return 0


def resolve_tau(c):
    """The threshold this session will use, and an honest account of it.

    Returns (tau_high, status) where status is calibration_status()'s dict.

    This deliberately does NOT persist anything. The previous version wrote the
    demo preset into cache/calibration.json whenever nothing was calibrated,
    which turned "we have no threshold" into a file that every later reader --
    including require_tau_high, whose entire job is to refuse that -- treated as
    an answer. The preset now lives for the length of one session and dies with
    it; only scripts/calibrate.py writes the sidecar.
    """
    status = calibration_status(c)
    if status["tau_high"] is not None:
        return float(status["tau_high"]), status
    return DEMO_TAU_HIGH, {
        "state": "uncalibrated",
        "tau_high": DEMO_TAU_HIGH,
        "source": "demo-preset (uncalibrated, see spec 8)",
        "detail": "Fixed %.1f-sigma preset, not measured performance. "
                  "Run scripts/calibrate.py against a labeled eval set."
                  % DEMO_TAU_HIGH,
    }


class BrowserFrameStream:
    """CameraStream-compatible source fed by browser JPEG POSTs (/api/frame).

    Used when the demo runs on a headless host (no /dev/video*) so the laptop
    webcam in the visitor's browser is the capture device.
    """

    def __init__(self, warmup_sec=15.0):
        self.warmup_sec = float(warmup_sec)
        self.latest = None
        self.lock = threading.Lock()
        self.stopped = False
        self.failure_reason = None
        self.captured = 0
        self.delivered = 0
        self._started_at = None
        self._watchdog = None

    def start(self):
        self._started_at = time.time()
        # Watchdog starts on first read() so a slow encoder/bank load before the
        # capture loop does not burn the warmup budget.
        return self

    def _ensure_watchdog(self):
        if self._watchdog is not None:
            return
        self._started_at = time.time()
        self._watchdog = threading.Thread(target=self._watch, name="browser-cam", daemon=True)
        self._watchdog.start()

    def _watch(self):
        while not self.stopped:
            with self.lock:
                got = self.captured
            if got > 0:
                return
            if time.time() - self._started_at >= self.warmup_sec:
                self.failure_reason = (
                    "no browser frames within %.0fs — allow camera access in the "
                    "browser (HTTPS required) and keep the tab visible"
                    % self.warmup_sec
                )
                self.stopped = True
                return
            time.sleep(0.1)

    def push_jpeg(self, data):
        if self.stopped or not data:
            return False
        import cv2
        import numpy as np

        arr = np.frombuffer(data, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            return False
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        with self.lock:
            self.captured += 1
            self.latest = rgb
        return True

    def read(self):
        self._ensure_watchdog()
        with self.lock:
            frame = self.latest
            self.latest = None
            if frame is not None:
                self.delivered += 1
            return frame

    @property
    def drop_rate(self):
        with self.lock:
            if self.captured == 0:
                return 0.0
            return 1.0 - (self.delivered / self.captured)

    def release(self):
        self.stopped = True
        if self._watchdog is not None:
            self._watchdog.join(timeout=1.0)


def _bind_ingest(stream):
    with STATE.lock:
        STATE.ingest_stream = stream


def _clear_ingest(stream=None):
    with STATE.lock:
        if stream is None or STATE.ingest_stream is stream:
            STATE.ingest_stream = None


def _open_stream(source, camera_index, live_cfg=None):
    """Open browser ingest or server OpenCV camera. source: 'browser' | 'server'."""
    source = (source or "browser").strip().lower()
    if source == "browser":
        warmup = 15.0
        if live_cfg is not None:
            warmup = float(live_cfg.get("camera_warmup_sec", 15.0))
            # Browser needs a little longer than a local device; floor at 10s.
            warmup = max(warmup, 10.0)
        stream = BrowserFrameStream(warmup_sec=warmup).start()
        _bind_ingest(stream)
        return stream
    if source != "server":
        raise RuntimeError("source must be 'browser' or 'server', got %r" % source)
    idx = 0 if camera_index is None else int(camera_index)
    cfg_live = live_cfg or {}
    return CameraStream(
        camera_index=idx,
        capture_fps=cfg_live.get("capture_fps", 30),
        warmup_sec=float(cfg_live.get("camera_warmup_sec", 5.0)),
    ).start()


# --------------------------------------------------------------------------
# Class / bank status
# --------------------------------------------------------------------------

def class_status(c):
    _, sid = bind_active_subject(c, require=False)
    if sid:
        refs_dir = subject_references_dir(c, sid)
    else:
        refs_dir = c.path("data", "references")
    names = set(c.class_names)
    if os.path.isdir(refs_dir):
        for entry in os.listdir(refs_dir):
            if not entry.startswith(".") and os.path.isdir(os.path.join(refs_dir, entry)):
                names.add(entry)

    bank_file = bank_path(c)
    bank_mtime = os.path.getmtime(bank_file) if os.path.exists(bank_file) else 0.0

    out = []
    for name in sorted(names):
        class_dir = os.path.join(refs_dir, name)
        clips = []
        if os.path.isdir(class_dir):
            clips = sorted(
                f for f in os.listdir(class_dir)
                if not f.startswith(".") and os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".avi", ".mkv")
            )
        newest_ref = max(
            (os.path.getmtime(os.path.join(class_dir, f)) for f in clips), default=0.0
        )
        out.append({
            "name": name,
            "n_references": len(clips),
            "ready": len(clips) > 0 and bank_mtime > 0 and bank_mtime >= newest_ref,
        })
    return out


@app.get("/api/state")
def api_state():
    c = cfg()
    subjects = _ensure_subjects(c)
    classes = class_status(c)
    # What live would actually use, including the in-memory preset -- so the UI
    # shows the number that will be applied, labelled with where it came from.
    tau_high, status = resolve_tau(c)
    active = STATE.active_subject_id
    active_meta = None
    face_ready = False
    n_refs = 0
    if active:
        active_meta = load_meta(c, active) or {"id": active, "display_name": active}
        face_ready = has_face_gallery(c, active)
        n_refs = count_reference_clips(c, active)
    return {
        # Events whose last row is `open` (spec 9.2): the run ended while a
        # detection was still running. Legitimate -- a daemon thread killed at
        # interpreter exit never reaches its flush -- but it must be visible,
        # because a reader that only counts `closed` rows silently loses them.
        "unclosed_events": _unclosed_count(c),
        "mode": STATE.mode,
        "classes": classes,
        "bank_built": os.path.exists(bank_path(c)),
        "tau_high": tau_high,
        "tau_high_source": status["source"],
        "calibration_state": status["state"],
        "calibration_detail": status["detail"],
        "clips_enabled": clips_enabled(c),
        "backbone": c.get("backbone"),
        "identity_enabled": identity_enabled(c),
        "subjects": [
            {
                **s,
                "n_references": count_reference_clips(c, s["id"]),
                "face_ready": has_face_gallery(c, s["id"]),
            }
            for s in subjects
        ],
        "active_subject": active_meta,
        "active_subject_id": active,
        "face_ready": face_ready,
        "n_subject_references": n_refs,
    }


# --------------------------------------------------------------------------
# Server-sent events -- pushes recording progress, build progress, and live
# open/close events to the page as they happen.
# --------------------------------------------------------------------------

@app.get("/api/stream")
def api_stream():
    q = queue.Queue()
    with STATE.lock:
        STATE.subscribers.append(q)

    def gen():
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                    yield "data: %s\n\n" % msg
                except queue.Empty:
                    yield ": keep-alive\n\n"
        finally:
            with STATE.lock:
                if q in STATE.subscribers:
                    STATE.subscribers.remove(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# --------------------------------------------------------------------------
# Step 1 -- add a reference clip: record from browser webcam (default) or
# server OpenCV camera, then run the prototype-bank build in the background
# (spec 6) and report readiness.
# --------------------------------------------------------------------------

def _record_loop(c, class_name, camera_index, fps, source="browser"):
    interval = 1.0 / float(fps)
    max_sec = float(c.get("prototypes", {}).get("max_reference_sec", 4.0))
    stream = None
    try:
        stream = _open_stream(source, camera_index, live_cfg=c.get("live"))
    except RuntimeError as exc:
        STATE.publish("record_error", {"message": str(exc)})
        with STATE.lock:
            STATE.mode = "idle"
        return

    STATE.publish("record_started", {"class_name": class_name, "max_sec": max_sec, "source": source})
    t0 = time.time()
    next_at = t0
    try:
        while not STATE.record_stop.is_set():
            if stream.stopped:
                STATE.publish("record_error", {"message": stream.failure_reason or "camera stopped"})
                # Hand the mode back here. Only api_record_stop advances it
                # otherwise, and the page disables Stop on record_error -- so a
                # camera that never delivers (browser permission denied, the
                # common case now that ingest is the default) would strand
                # mode="recording" and 409 "busy" on every later start.
                with STATE.lock:
                    STATE.mode = "idle"
                break
            now = time.time()
            if now < next_at:
                time.sleep(0.005)
                continue
            frame = stream.read()
            if frame is None:
                time.sleep(0.005)
                continue
            next_at += interval
            elapsed = now - t0
            STATE.record_frames.append((elapsed, frame))
            STATE.publish("record_progress", {"elapsed": round(elapsed, 1), "max_sec": max_sec})
            # Auto-stop at max_reference_sec so W_base cannot balloon again.
            if elapsed >= max_sec:
                STATE.record_stop.set()
                break
    finally:
        stream.release()
        _clear_ingest(stream)


@app.post("/api/record/start")
def api_record_start(class_name: str, camera_index: int = 0, source: str = "browser"):
    with STATE.lock:
        if STATE.mode != "idle":
            raise HTTPException(409, "busy: current mode is %r" % STATE.mode)
        class_name = class_name.strip()
        if not class_name or not class_name.replace("_", "").isalnum():
            raise HTTPException(400, "class name must be alphanumeric/underscore")
        source = (source or "browser").strip().lower()
        if source not in ("browser", "server"):
            raise HTTPException(400, "source must be 'browser' or 'server'")
        STATE.mode = "recording"
        STATE.record_class = class_name
        STATE.record_frames = []
        STATE.record_stop = threading.Event()

    c = cfg()
    STATE.record_thread = threading.Thread(
        target=_record_loop, args=(c, class_name, camera_index, c["working_fps"], source), daemon=True
    )
    STATE.record_thread.start()
    return {"ok": True, "source": source}


@app.post("/api/record/stop")
def api_record_stop(save: bool = True):
    with STATE.lock:
        if STATE.mode != "recording":
            raise HTTPException(409, "not currently recording")
        class_name = STATE.record_class

    STATE.record_stop.set()
    if STATE.record_thread is not None:
        STATE.record_thread.join(timeout=5.0)

    # Snapshot AFTER the recorder thread exits so we keep the last frames
    # (copying before join dropped the tail; copying while empty looked like
    # a successful stop with nothing on disk).
    with STATE.lock:
        frames = [f for _, f in STATE.record_frames]
        # Keep the last preview frame until the next session; clearing it here
        # raced the UI poll and produced a burst of empty responses.
        STATE.record_frames = []

    c = cfg()
    min_useful_sec = float(c["frames_per_clip"]) / float(c["working_fps"])
    duration = len(frames) / float(c["working_fps"]) if frames else 0.0

    if not save or not frames:
        with STATE.lock:
            STATE.mode = "idle"
        return {
            "ok": True,
            "saved": False,
            "duration_sec": round(duration, 2),
            "warning": (
                None if frames
                else "no frames captured -- check camera index / permissions"
            ),
        }

    class_dir = c.path("data", "references", class_name)
    try:
        c, sid = bind_active_subject(c, require=identity_enabled(c))
        if sid:
            class_dir = os.path.join(subject_references_dir(c, sid), class_name)
        os.makedirs(class_dir, exist_ok=True)
        out_path = os.path.join(class_dir, "ref_%s.mp4" % time.strftime("%Y%m%dT%H%M%S"))
        write_frames(out_path, frames, c["working_fps"])
    except Exception:
        # Mode was still "recording" (only the success path below advances it
        # to "building"), so a write failure here would otherwise strand it --
        # every future record/live start then 409s "busy" until a restart.
        with STATE.lock:
            STATE.mode = "idle"
        raise

    warning = None
    if duration < min_useful_sec:
        warning = (
            "this clip is %.1fs, below the %.1fs floor (frames_per_clip / working_fps) "
            "-- it will be padded with repeated frames and similarity will suffer. "
            "Consider re-recording longer." % (duration, min_useful_sec)
        )

    with STATE.lock:
        STATE.mode = "building"
    threading.Thread(target=_build_loop, args=(c, sid), daemon=True).start()

    return {"ok": True, "saved": True, "path": out_path, "duration_sec": round(duration, 2), "warning": warning}


@app.post("/api/references/upload")
async def api_references_upload(class_name: str, file: UploadFile):
    """Fallback path: upload a clip instead of recording one live."""
    with STATE.lock:
        if STATE.mode != "idle":
            raise HTTPException(409, "busy: current mode is %r" % STATE.mode)
        class_name = class_name.strip()
        if not class_name or not class_name.replace("_", "").isalnum():
            raise HTTPException(400, "class name must be alphanumeric/underscore")
        STATE.mode = "building"

    c = cfg()
    try:
        c, sid = bind_active_subject(c, require=identity_enabled(c))
        class_dir = (
            os.path.join(subject_references_dir(c, sid), class_name)
            if sid else c.path("data", "references", class_name)
        )
        os.makedirs(class_dir, exist_ok=True)
        ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
        out_path = os.path.join(class_dir, "ref_%s%s" % (time.strftime("%Y%m%dT%H%M%S"), ext))
        with open(out_path, "wb") as fh:
            fh.write(await file.read())
    except HTTPException:
        with STATE.lock:
            STATE.mode = "idle"
        raise
    except Exception:
        # Mode was already set to "building" above; a write failure here would
        # otherwise strand it, since nothing else resets it back to "idle".
        # Also covers migrate/create_subject mkdir failures under subjects/.
        with STATE.lock:
            STATE.mode = "idle"
        raise

    threading.Thread(target=_build_loop, args=(c, sid), daemon=True).start()
    return {"ok": True, "path": out_path}


def _build_loop(c, subject_id=None):
    """The background work: encode every class's references into the
    prototype bank (spec 6), same as scripts/build_prototypes.py -- including
    the pose/hand template stages when those streams are enabled, so a config
    that turns them on gets templates the live loop can actually load."""
    try:
        if subject_id:
            bind_subject(c, subject_id)
        refs = c.get("_references_dir")
        if c.get("pose", {}).get("enabled", False):
            STATE.publish("build_progress", {"message": "building pose templates..."})
            from src.pose import build_pose_templates, save_pose_templates

            pose_templates = build_pose_templates(c, references_dir=refs, verbose=False)
            if pose_templates:
                save_pose_templates(c, pose_templates)

        if c.get("hands", {}).get("enabled", False):
            STATE.publish("build_progress", {"message": "building hand templates..."})
            from src.hands import build_hand_templates, save_hand_templates

            hand_templates = build_hand_templates(c, references_dir=refs, verbose=False)
            if hand_templates:
                save_hand_templates(c, hand_templates)

        STATE.publish("build_progress", {"message": "building prototype bank..."})
        encoder = get_encoder(c)
        bank, w_base_sec = build_bank(c, encoder, references_dir=refs, verbose=False)
        save_bank(c, bank, w_base_sec)
        face_n = 0
        if subject_id and identity_enabled(c):
            STATE.publish("build_progress", {"message": "harvesting face gallery..."})
            try:
                face_n = harvest_subject_faces(c, subject_id, verbose=False)
            except Exception as exc:  # noqa: BLE001
                STATE.publish("build_progress", {
                    "message": "face harvest warning: %s" % exc,
                })
        STATE.publish("build_done", {
            "message": "ready for detection",
            "classes": sorted(bank.keys()),
            "w_base_sec": round(w_base_sec, 2),
            "subject_id": subject_id,
            "face_embeddings": face_n,
        })
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        # SystemExit, not just Exception: src.pose.build_pose_templates and
        # src.prototypes.load_bank/build_bank raise it for their fatal,
        # user-facing errors (idiomatic in the CLI scripts they were written
        # for). It is a BaseException, not an Exception, so it slips past a
        # bare `except Exception` -- the thread would then die silently with
        # no build_error reaching the page.
        STATE.publish("build_error", {"message": str(exc)})
    finally:
        with STATE.lock:
            STATE.mode = "idle"


# --------------------------------------------------------------------------
# Step 2 -- live test. Runs the real live detector (src/live.py) against the
# browser webcam (default) or the server's OpenCV camera. Re-implements
# run_live()'s loop rather than calling it directly, because run_live()
# installs a SIGINT handler (main-thread only) and this runs in a background
# thread; stop is a plain Event instead.
# --------------------------------------------------------------------------

def _live_loop(c, camera_index, max_seconds, source="browser"):
    started_at = utc_now()
    session_id = live_session_id(started_at)
    STATE.live_session_id = session_id
    stream = None
    face_encoder = None
    subject_id = STATE.active_subject_id
    source = (source or "browser").strip().lower()

    # Bind browser ingest BEFORE the slow encoder/bank load. Otherwise the
    # page starts POSTing /api/frame immediately, gets 409 (no ingest yet),
    # and may give up — leaving mode="detecting" with no camera frames.
    if source == "browser":
        try:
            stream = _open_stream(source, camera_index, live_cfg=c.get("live"))
        except RuntimeError as exc:
            STATE.publish("live_error", {"message": str(exc)})
            with STATE.lock:
                STATE.mode = "idle"
            return

    try:
        if subject_id:
            bind_subject(c, subject_id)
        elif identity_enabled(c):
            raise RuntimeError("select an active subject before starting live")

        encoder = get_encoder(c)
        from src.prototypes import load_bank

        bank, w_base_sec = load_bank(c)
        c = load_config(CONFIG_PATH, w_base_sec=w_base_sec)
        if subject_id:
            bind_subject(c, subject_id)
        tau_high, tau_status = resolve_tau(c)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        # SystemExit, not just Exception: load_bank raises it (missing bank,
        # or a bank file present but containing no classes -- e.g. corrupted
        # or left over from an aborted build). /api/live/start already checks
        # the bank file exists, but not that it's non-empty, so this can still
        # be hit. Uncaught, STATE.mode would stay stuck at "detecting" forever
        # (there is no `finally` here) -- every future record/live start then
        # 409s "busy" until the server is restarted.
        STATE.publish("live_error", {"message": str(exc)})
        if stream is not None:
            stream.release()
            _clear_ingest(stream)
        with STATE.lock:
            STATE.mode = "idle"
        return

    def on_event(kind, event):
        start = event.get("start")
        end = event.get("end")
        duration = None
        if start is not None and end is not None:
            duration = float(end) - float(start)
        STATE.publish("live_" + kind, {
            "event_id": event.get("event_id"),
            "class": event.get("class"),
            "start": start,
            "end": end,
            "duration_sec": duration,
            "score": event.get("score"),
            "clip_path": event.get("clip_path"),
            "forced": event.get("forced", False),
        })

    writer = EventLogWriter(
        path=c.path(c["event_log"]["path"]),
        run_id=new_run_id(),
        source_id=session_id,
        source_type=SOURCE_LIVE,
        model_id=c["model_id"],
        working_fps=c["working_fps"],
        chunk_sec=c["chunk_sec"],
        tau_high=tau_high,
        source_start_utc=started_at,
        flush_each_event=bool(c["event_log"]["flush_each_event"]),
        subject_id=subject_id,
    )

    live_cfg = c["live"]
    if camera_index is None:
        camera_index = live_cfg["camera_index"]
    try:
        if stream is None:
            stream = _open_stream(source, camera_index, live_cfg=live_cfg)
    except RuntimeError as exc:
        STATE.publish("live_error", {"message": str(exc)})
        writer.close()
        with STATE.lock:
            STATE.mode = "idle"
        return

    # Same streams as the offline path and scripts/detect_live.py. Without
    # these, enabling pose/hands in config.yaml would silently do nothing in
    # the web demo -- see src.live.run_live for the reference wiring.
    pose_templates = pose_extractor = None
    try:
        pose_templates, pose_extractor = load_live_pose_extractor(c)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        # Fatal only when the landmarker changes what gets DETECTED -- the DTW
        # stream, or a class gating its opens on wrist-near-ear. When the only
        # caller is the preview overlay the failure is cosmetic, and killing
        # the session over a missing drawing would be the wrong trade.
        if pose_required_for_scoring(c):
            STATE.publish("live_error", {
                "message": "pose landmarker failed to load (ear_cover wrist gate): %s" % exc,
            })
            stream.release()
            _clear_ingest(stream)
            writer.close()
            with STATE.lock:
                STATE.mode = "idle"
            return
        STATE.publish("status", {
            "message": "skeleton overlay unavailable: %s" % exc,
        })
    hand_templates = hand_extractor = None
    try:
        if c.get("hands", {}).get("enabled", False):
            from src.hands import HandExtractor, load_hand_templates

            hand_templates = load_hand_templates(c)
            if hand_templates:
                hand_extractor = HandExtractor(
                    model_path=c["hands"].get("model_path") or None,
                    min_confidence=c["hands"].get("min_confidence", 0.4),
                )
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        STATE.publish("live_error", {"message": "hand landmarker failed to load: %s" % exc})
        stream.release()
        _clear_ingest(stream)
        writer.close()
        with STATE.lock:
            STATE.mode = "idle"
        return

    # Calibrated background scale (see src.normalize.RunningBackground): without
    # this the demo always estimates spread from a cold running window, even
    # after scripts/calibrate.py has measured the real value.
    background_scale = None
    if bool(c.get("live", {}).get("use_calibrated_scale", False)):
        sidecar = c.path("cache", "calibration.json")
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8") as fh:
                background_scale = json.load(fh).get("background_scale")

    frame_buffer = store = None
    detector = None
    t0 = time.time()
    try:
        face_gallery = None
        if identity_enabled(c):
            if not subject_id:
                raise RuntimeError("identity.enabled requires an active subject")
            face_gallery = load_gallery(c, subject_id)
            if face_gallery is None:
                raise RuntimeError(
                    "no face gallery for subject %r -- record a reference clip first"
                    % subject_id
                )
            face_encoder = FaceIdEncoder(c)

        detector = LiveDetector(
            c, encoder, bank, tau_high, writer, on_event, session_id=session_id,
            pose_templates=pose_templates, hand_templates=hand_templates,
            background_scale=background_scale,
            subject_id=subject_id,
            face_gallery=face_gallery,
            face_encoder=face_encoder,
        )

        # The buffer is sized from the detector, not re-derived from config:
        # this loop and src.live.run_live had drifted to different constants,
        # so browser and CLI sessions kept different amounts of pre-roll.
        # Detector first, then store, then buffer -- retention_ceiling_sec is
        # anchored on the store's max_clip_sec.
        if clips_enabled(c):
            store = build_store(c)
            detector.clip_store = store
            frame_buffer = FrameRetentionBuffer(
                base_horizon_sec=detector.retention_horizon_sec,
                hard_ceiling_sec=detector.retention_ceiling_sec,
                jpeg_quality=c["clips"].get("jpeg_quality", 80),
            )
            detector.frame_buffer = frame_buffer

        # Overlay settings. Rendering only -- it never touches active_streams.
        overlay_cfg = c.get("overlay", {})
        overlay_on = bool(overlay_cfg.get("enabled", False)) and pose_extractor is not None
        overlay_mirror = bool(overlay_cfg.get("mirror", True))
        overlay_min_vis = float(overlay_cfg.get("min_visibility", 0.5))
        overlay_stride = max(1, int(overlay_cfg.get("stride", 1)))

        STATE.publish("live_started", {
            "session_id": session_id,
            "classes": detector.class_names,
            "streams": detector.active_streams,
            "tau_high": tau_high,
            "tau_high_source": tau_status["source"],
            "calibration_state": tau_status["state"],
            "calibration_detail": tau_status["detail"],
            "clips_enabled": store is not None,
            "warmup_sec": detector.warmup_chunks * detector.chunk_sec,
            "source": source,
            # Topology ships from the server so the client cannot drift from
            # UPPER_BODY, and mirror ships so CSS and config stay in step.
            "overlay": {
                "enabled": overlay_on,
                "mirror": overlay_mirror,
                "max_age_ms": int(overlay_cfg.get("max_age_ms", 1200)),
                "edges": POSE_EDGES,
            },
        })

        t0 = time.time()
        frame_interval = 1.0 / float(c["working_fps"])
        next_frame_at = t0
        buffer = []
        chunk_index = 0
        frame_index = 0
        pose_seq_no = 0
        warmed = False

        while not STATE.live_stop.is_set():
            if max_seconds is not None and (time.time() - t0) >= max_seconds:
                break
            if stream.stopped:
                STATE.publish("live_error", {"message": stream.failure_reason or "camera stopped delivering frames"})
                break

            now = time.time()
            if now < next_frame_at:
                time.sleep(min(0.005, next_frame_at - now))
                continue

            frame = stream.read()
            if frame is None:
                time.sleep(0.002)
                continue

            capture_sec = now - t0
            if detector is not None:
                try:
                    detector.update_face_match(frame)
                except Exception:  # noqa: BLE001
                    pass

            # Pose PER FRAME, not per chunk.
            #
            # Computing it at chunk time (after the encode) made landmarks
            # describing t in [T, T+2] available at T+2+encode -- 2 to 3.5s
            # stale. A skeleton drawn from that puts a hand at an ear seconds
            # after the real hand came down.
            #
            # It costs the same: landmarks_for_clip already ran detect() on all
            # 16 frames of every 2s chunk, which is the same 8/sec as one per
            # frame at working_fps. Measured 20.9 ms/frame on an M3, vs a
            # 125 ms budget. The chunk-time call below is now a fallback only.
            raw_landmarks = None
            if pose_extractor is not None and frame_index % overlay_stride == 0:
                try:
                    xyz, vis = pose_extractor.detect_frame(frame)
                    raw_landmarks = xyz
                    if overlay_on:
                        pose_seq_no = pose_seq_no + 1
                        STATE.publish("pose_frame", {
                            "seq": pose_seq_no,
                            "t": round(capture_sec, 3),
                            "points": landmarks_to_overlay(
                                xyz, vis,
                                min_visibility=overlay_min_vis,
                                mirror=overlay_mirror),
                        })
                except Exception:  # noqa: BLE001
                    # Drawing must never take the session down.
                    raw_landmarks = None
            frame_index += 1

            buffer.append((capture_sec, frame, raw_landmarks))
            if frame_buffer is not None:
                frame_buffer.append(capture_sec, frame)
            next_frame_at += frame_interval
            if next_frame_at < now:
                next_frame_at = now + frame_interval

            if len(buffer) >= detector.frames_per_chunk:
                taken = buffer[: detector.frames_per_chunk]
                buffer = buffer[detector.frames_per_chunk:]
                start_sec = taken[0][0]
                end_sec = taken[-1][0] + frame_interval

                import numpy as np
                clip = np.stack([f for _, f, _ in taken])
                feature = encoder.encode_clips([clip])[0]
                pose_seq = hand_seq = None
                if pose_extractor is not None:
                    from src.pose import normalize_pose

                    # Reuse the per-frame landmarks rather than re-running the
                    # detector over the same frames. Falls back to the chunk
                    # call if stride skipped frames or any detect_frame failed,
                    # so the wrist gate sees the same input either way.
                    rows = [lm for _, _, lm in taken]
                    if all(lm is not None for lm in rows):
                        pose_seq = normalize_pose(np.stack(rows))
                    else:
                        pose_seq = normalize_pose(pose_extractor.landmarks_for_clip(clip))
                if hand_extractor is not None:
                    from src.hands import normalize_hand

                    hand_seq = normalize_hand(hand_extractor.landmarks_for_clip(clip))
                detector.push_chunk(feature, start_sec, end_sec, pose_seq, hand_seq)
                chunk_index += 1
                # Gate the UI on the real RunningBackground.ready flag, not
                # chunk count alone -- flat early chunks can leave MAD at 0
                # past the nominal warmup and the detector still refuses opens.
                if not warmed and detector.ready:
                    warmed = True
                    STATE.publish("live_warmed_up", {
                        "chunks": chunk_index,
                        "warmup_chunks": detector.warmup_chunks,
                    })
    except Exception as exc:  # noqa: BLE001
        STATE.publish("live_error", {"message": "live loop crashed: %s" % exc})
    finally:
        if detector is not None:
            try:
                detector.flush(time.time() - t0)
            except Exception as exc:  # noqa: BLE001
                # Never silent. A failed flush leaves `open` rows with no
                # matching `closed` row, and the log is append-only so nothing
                # downstream can tell that apart from a crashed run.
                # (A killed daemon thread skips this block entirely -- that
                # case is spec 9.2 and is reported at startup instead.)
                print("WARNING: flush failed, events left open: %s" % exc)
                STATE.publish("live_error", {
                    "message": "session ended but events could not be closed: %s" % exc,
                })
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            stream.release()
        except Exception:  # noqa: BLE001
            pass
        if face_encoder is not None:
            try:
                face_encoder.close()
            except Exception:  # noqa: BLE001
                pass
        _clear_ingest(stream)
        for extractor in (pose_extractor, hand_extractor):
            if extractor is not None:
                try:
                    extractor.close()
                except Exception:  # noqa: BLE001
                    pass
        if store is not None:
            try:
                store.enforce_budget()
            except Exception:  # noqa: BLE001
                pass
        if frame_buffer is not None:
            try:
                frame_buffer.clear()
            except Exception:  # noqa: BLE001
                pass
        with STATE.lock:
            STATE.mode = "idle"
        STATE.publish("live_stopped", {"session_id": session_id})


@app.post("/api/live/start")
def api_live_start(
    camera_index: Optional[int] = None,
    max_seconds: Optional[float] = None,
    source: str = "browser",
):
    c = cfg()
    c, sid = bind_active_subject(c, require=identity_enabled(c))
    if identity_enabled(c):
        if not has_face_gallery(c, sid):
            raise HTTPException(
                409,
                "no face gallery for the active subject -- record a reference clip first",
            )
    if not os.path.exists(bank_path(c)):
        raise HTTPException(409, "no prototype bank yet -- add at least one reference clip first")
    source = (source or "browser").strip().lower()
    if source not in ("browser", "server"):
        raise HTTPException(400, "source must be 'browser' or 'server'")
    with STATE.lock:
        if STATE.mode != "idle":
            raise HTTPException(409, "busy: current mode is %r" % STATE.mode)
        STATE.mode = "detecting"
        STATE.live_stop = threading.Event()

    STATE.live_thread = threading.Thread(
        target=_live_loop, args=(c, camera_index, max_seconds, source), daemon=True
    )
    STATE.live_thread.start()
    return {"ok": True, "source": source, "subject_id": sid}


@app.post("/api/live/stop")
def api_live_stop():
    with STATE.lock:
        mode = STATE.mode
        thread = STATE.live_thread
        stop_evt = STATE.live_stop
    if mode != "detecting":
        # Idempotent: UI may call stop after a refresh when already idle.
        return {"ok": True, "mode": mode}
    stop_evt.set()
    # If the worker already died without clearing mode, force idle so Start
    # is not stuck behind a ghost "detecting" session.
    if thread is not None and not thread.is_alive():
        _clear_ingest()
        with STATE.lock:
            STATE.mode = "idle"
        STATE.publish("live_stopped", {"session_id": STATE.live_session_id})
    return {"ok": True}


@app.post("/api/frame")
async def api_frame(request: Request):
    """Accept one JPEG frame from the browser webcam while recording/detecting."""
    with STATE.lock:
        stream = STATE.ingest_stream
        mode = STATE.mode
    if stream is None or mode not in ("recording", "detecting"):
        raise HTTPException(409, "not currently ingesting browser frames")
    body = await request.body()
    if not body:
        raise HTTPException(400, "empty body")
    if not stream.push_jpeg(body):
        raise HTTPException(400, "could not decode JPEG frame")
    return {"ok": True}


# --------------------------------------------------------------------------
# Step 3 -- saved clips, grouped by class, playable.
# --------------------------------------------------------------------------

# Analytics route parked with the UI tab for now. Uncomment the decorator to restore.
# @app.get("/api/analytics")
def api_analytics():
    """Aggregate detection results for the Analytics tab.

    Uses the full event log (closed rows), including events whose clips were
    deleted — the audit trail is the source of truth for counts and scores.
    Includes per-day trigger counts so the UI can show activity over time.
    """
    from datetime import date, datetime, timezone

    c = cfg()
    path = c.path(c["event_log"]["path"])
    events = read_events(path) if os.path.exists(path) else []
    closed = [e for e in events if e.get("status") == "closed"]
    opens = [e for e in events if e.get("status") == "open"]

    def _as_utc(value):
        if value is None:
            return None
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        if isinstance(value, str) and value:
            text = value.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(text)
            except ValueError:
                return None
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        return None

    def _day_key(event):
        dt = _as_utc(event.get("start_utc")) or _as_utc(event.get("written_utc"))
        if dt is None:
            return None
        return dt.astimezone(timezone.utc).date().isoformat()

    def _stats(values):
        if not values:
            return {"mean": None, "median": None, "min": None, "max": None, "sum": 0.0}
        arr = sorted(values)
        n = len(arr)
        mid = n // 2
        median = arr[mid] if n % 2 else 0.5 * (arr[mid - 1] + arr[mid])
        return {
            "mean": sum(arr) / n,
            "median": median,
            "min": arr[0],
            "max": arr[-1],
            "sum": sum(arr),
        }

    by_class = {}
    for e in closed:
        name = e.get("class") or "unknown"
        bucket = by_class.setdefault(name, {
            "count": 0,
            "durations": [],
            "scores": [],
            "with_clip": 0,
        })
        bucket["count"] += 1
        if e.get("duration_sec") is not None:
            bucket["durations"].append(float(e["duration_sec"]))
        if e.get("score") is not None:
            bucket["scores"].append(float(e["score"]))
        clip = e.get("clip_path")
        if clip and os.path.exists(c.path(clip)):
            bucket["with_clip"] += 1

    classes_out = {}
    for name, bucket in by_class.items():
        d = _stats(bucket["durations"])
        s = _stats(bucket["scores"])
        classes_out[name] = {
            "count": bucket["count"],
            "with_clip": bucket["with_clip"],
            "duration_sec": d,
            "score": s,
        }

    by_day = {}
    for e in closed:
        day = _day_key(e)
        if day is None:
            continue
        bucket = by_day.setdefault(day, {
            "count": 0,
            "duration_sum": 0.0,
            "by_class": {},
        })
        bucket["count"] += 1
        if e.get("duration_sec") is not None:
            bucket["duration_sum"] += float(e["duration_sec"])
        cn = e.get("class") or "unknown"
        bucket["by_class"][cn] = bucket["by_class"].get(cn, 0) + 1

    days_out = [
        {
            "date": day,
            "count": info["count"],
            "duration_sum": info["duration_sum"],
            "by_class": info["by_class"],
        }
        for day, info in sorted(by_day.items(), reverse=True)
    ]

    today = date.today().isoformat()
    today_row = by_day.get(today, {"count": 0, "duration_sum": 0.0, "by_class": {}})
    # Last 14 days inclusive, oldest → newest for charting.
    from datetime import timedelta
    last_14 = []
    for i in range(13, -1, -1):
        d = (date.today() - timedelta(days=i)).isoformat()
        info = by_day.get(d, {"count": 0, "duration_sum": 0.0, "by_class": {}})
        last_14.append({
            "date": d,
            "count": info["count"],
            "duration_sum": info["duration_sum"],
            "by_class": info["by_class"],
        })

    sessions = {}
    for e in closed:
        sid = e.get("source_id") or "unknown"
        sess = sessions.setdefault(sid, {"count": 0, "duration_sum": 0.0, "classes": {}})
        sess["count"] += 1
        if e.get("duration_sec") is not None:
            sess["duration_sum"] += float(e["duration_sec"])
        cn = e.get("class") or "unknown"
        sess["classes"][cn] = sess["classes"].get(cn, 0) + 1

    def _sort_key(row):
        written = row.get("written_utc") or row.get("start_utc")
        dt = _as_utc(written)
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    recent = []
    for e in sorted(closed, key=_sort_key, reverse=True)[:40]:
        recent.append({
            "event_id": e.get("event_id"),
            "class": e.get("class"),
            "source_id": e.get("source_id"),
            "duration_sec": e.get("duration_sec"),
            "score": e.get("score"),
            "start_utc": e["start_utc"].isoformat() if e.get("start_utc") else None,
            "end_utc": e["end_utc"].isoformat() if e.get("end_utc") else None,
            "day": _day_key(e),
            "has_clip": bool(e.get("clip_path") and os.path.exists(c.path(e["clip_path"]))),
        })

    total_duration = sum(
        float(e["duration_sec"]) for e in closed if e.get("duration_sec") is not None
    )
    return {
        "total_closed": len(closed),
        "total_open": len(opens),
        "total_duration_sec": total_duration,
        "today": {
            "date": today,
            "count": today_row["count"],
            "duration_sum": today_row["duration_sum"],
            "by_class": today_row["by_class"],
        },
        "by_day": days_out,
        "last_14_days": last_14,
        "by_class": classes_out,
        "sessions": [
            {"source_id": sid, **data}
            for sid, data in sorted(
                sessions.items(), key=lambda kv: kv[0], reverse=True
            )
        ],
        "recent": recent,
    }


@app.get("/api/events")
def api_events():
    """Gallery payload: only closed events whose clip file still exists.

    Delete removes the file but leaves the event-log row (audit trail). The
    Captures UI must not show ghost "No file" placeholders for those rows.
    """
    c = cfg()
    events = read_events(c.path(c["event_log"]["path"]))
    by_class = {}
    for e in events:
        if e["status"] != "closed":
            continue
        clip_path = e["clip_path"]
        if not clip_path or not os.path.exists(c.path(clip_path)):
            # Retention or a user delete removed the file; skip (spec 9.6).
            continue
        by_class.setdefault(e["class"], []).append({
            "event_id": e["event_id"],
            "source_id": e["source_id"],
            "source_type": e["source_type"],
            "start_sec": e["start_sec"],
            "end_sec": e["end_sec"],
            "duration_sec": e["duration_sec"],
            "score": e["score"],
            "start_utc": e["start_utc"].isoformat() if e["start_utc"] else None,
            "clip_url": "/api/clip/%s" % e["event_id"],
        })
    for rows in by_class.values():
        rows.sort(key=lambda r: r["start_utc"] or "", reverse=True)
    return by_class


@app.get("/api/clip/{event_id}")
@app.head("/api/clip/{event_id}")
def api_clip(event_id: str):
    c = cfg()
    events = {e["event_id"]: e for e in read_events(c.path(c["event_log"]["path"]))}
    e = events.get(event_id)
    if e is None or not e["clip_path"]:
        raise HTTPException(404, "no clip for this event")
    full = c.path(e["clip_path"])
    if not os.path.exists(full):
        raise HTTPException(404, "clip_path is on record but the file is gone (retention, spec 9.6)")
    return FileResponse(full, media_type="video/mp4")


def _delete_clip_file(c, event_id):
    events = {e["event_id"]: e for e in read_events(c.path(c["event_log"]["path"]))}
    e = events.get(event_id)
    if e is None:
        raise HTTPException(404, "unknown event_id")
    clip_path = e.get("clip_path")
    if not clip_path:
        raise HTTPException(404, "event has no clip_path")
    full = c.path(clip_path)
    if os.path.exists(full):
        os.remove(full)
        return {"ok": True, "deleted": True, "path": clip_path}
    return {"ok": True, "deleted": False, "path": clip_path, "message": "file already gone"}


@app.post("/api/clip/{event_id}/delete")
def api_clip_delete(event_id: str):
    """Remove one saved detection clip from disk (event log row stays)."""
    return _delete_clip_file(cfg(), event_id)


@app.post("/api/clips/delete_all")
def api_clips_delete_all():
    """Remove every clip file still on disk under data/clips/."""
    c = cfg()
    clips_root = c.path("data", "clips")
    removed = 0
    if os.path.isdir(clips_root):
        for root, _dirs, files in os.walk(clips_root):
            for name in files:
                if name.startswith("."):
                    continue
                path = os.path.join(root, name)
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
    return {"ok": True, "removed": removed}


@app.post("/api/prototypes/rebuild")
def api_prototypes_rebuild():
    """Manual rebuild of the prototype bank (same job as after recording)."""
    with STATE.lock:
        if STATE.mode != "idle":
            raise HTTPException(409, "busy: current mode is %r" % STATE.mode)
        STATE.mode = "building"
    c = cfg()
    c, sid = bind_active_subject(c, require=identity_enabled(c))
    threading.Thread(target=_build_loop, args=(c, sid), daemon=True).start()
    return {"ok": True, "subject_id": sid}


@app.get("/api/subjects")
def api_subjects_list():
    c = cfg()
    subjects = _ensure_subjects(c)
    return {
        "active_subject_id": STATE.active_subject_id,
        "identity_enabled": identity_enabled(c),
        "subjects": [
            {
                **s,
                "n_references": count_reference_clips(c, s["id"]),
                "face_ready": has_face_gallery(c, s["id"]),
            }
            for s in subjects
        ],
    }


@app.post("/api/subjects")
def api_subjects_create(display_name: str, subject_id: Optional[str] = None):
    c = cfg()
    _ensure_subjects(c)
    try:
        meta = create_subject(c, display_name, subject_id=subject_id)
    except (ValueError, FileExistsError) as exc:
        raise HTTPException(400, str(exc)) from exc
    with STATE.lock:
        STATE.active_subject_id = meta["id"]
    return {"ok": True, "subject": meta}


@app.post("/api/subjects/{subject_id}/select")
def api_subjects_select(subject_id: str):
    c = cfg()
    _ensure_subjects(c)
    meta = load_meta(c, subject_id)
    if meta is None:
        raise HTTPException(404, "subject not found: %s" % subject_id)
    with STATE.lock:
        if STATE.mode != "idle":
            raise HTTPException(409, "busy: current mode is %r" % STATE.mode)
        STATE.active_subject_id = subject_id
    return {"ok": True, "subject": meta}


@app.patch("/api/subjects/{subject_id}")
def api_subjects_rename(subject_id: str, display_name: str):
    c = cfg()
    meta = load_meta(c, subject_id)
    if meta is None:
        raise HTTPException(404, "subject not found: %s" % subject_id)
    meta["display_name"] = (display_name or "").strip() or meta["display_name"]
    save_meta(c, meta)
    return {"ok": True, "subject": meta}


@app.delete("/api/subjects/{subject_id}")
def api_subjects_delete(subject_id: str):
    c = cfg()
    with STATE.lock:
        if STATE.mode != "idle":
            raise HTTPException(409, "busy: current mode is %r" % STATE.mode)
        if STATE.active_subject_id == subject_id:
            STATE.active_subject_id = None
    try:
        delete_subject(c, subject_id)
    except FileNotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"ok": True}


@app.post("/api/classes")
def api_classes_create(class_name: str):
    """Register a new use case: a config.yaml `classes:` entry (the source of
    truth build_bank/live detection actually read) plus its reference folder.

    Without the config.yaml entry, a class recorded here would sit on disk
    forever invisible to build_bank -- cfg.class_names comes from config.yaml,
    not from what directories happen to exist under data/references/.
    """
    class_name = class_name.strip()
    if not class_name or not class_name.replace("_", "").isalnum():
        raise HTTPException(400, "class name must be alphanumeric/underscore")
    if class_name.startswith("__"):
        raise HTTPException(400, "class name may not start with '__' (reserved)")
    c = cfg()
    add_class_to_config(c, class_name)
    c, sid = bind_active_subject(c, require=False)
    if sid:
        class_dir = os.path.join(subject_references_dir(c, sid), class_name)
    else:
        class_dir = c.path("data", "references", class_name)
    os.makedirs(class_dir, exist_ok=True)
    return {"ok": True, "name": class_name, "path": class_dir}


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
DEFAULT_HOST = "127.0.0.1"


def is_loopback(host):
    """An empty host is NOT loopback: uvicorn binds every interface for it.

    `HOST= python webapp/server.py` reaches asyncio as host="", which listens
    on 0.0.0.0 just like the value this gate exists to refuse. An unset HOST is
    handled by resolve_host() instead, which substitutes the loopback default.
    """
    return str(host).strip().lower() in LOOPBACK_HOSTS


def resolve_host(value):
    """HOST env -> bind address. Unset or blank means the loopback default."""
    return (value or "").strip() or DEFAULT_HOST


def check_exposure(host, token):
    """Refuse to bind a non-loopback interface without a shared token.

    Several routes are destructive to anyone who can reach them:
    /api/clips/delete_all removes every saved clip, and /api/classes REWRITES
    config.yaml. There is no auth, no CORS policy and no rate limiting, and the
    README points at EC2 -- so HOST=0.0.0.0 is one env var away from exposing
    all of that, plus a live camera feed, to the network.

    This is not an auth system. It is a gate that makes exposing the demo a
    deliberate act rather than an accident.
    """
    if is_loopback(host) or token:
        return
    raise SystemExit(
        "refusing to bind HOST=%s without APP_TOKEN.\n"
        "\n"
        "This app has no authentication. On a non-loopback interface, anyone\n"
        "who can reach it can delete every saved clip (/api/clips/delete_all),\n"
        "rewrite config.yaml (/api/classes), and watch the camera.\n"
        "\n"
        "  APP_TOKEN=$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))') \\\n"
        "  HOST=%s python webapp/server.py\n"
        "\n"
        "Then send it as `Authorization: Bearer $APP_TOKEN` on POST/DELETE.\n"
        "Or leave HOST unset to stay on 127.0.0.1." % (host, host)
    )


if __name__ == "__main__":
    import uvicorn

    host = resolve_host(os.environ.get("HOST"))
    port = int(os.environ.get("PORT", "8000"))
    check_exposure(host, APP_TOKEN)
    if APP_TOKEN and not is_loopback(host):
        print("auth      : APP_TOKEN required on mutating requests")
    uvicorn.run(app, host=host, port=port)

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
labeled-eval-set sweep (scripts/calibrate.py), a fixed sigma threshold is
written to cache/calibration.json if one is not already there. This is called
out in the UI as an uncalibrated demo threshold, never presented as measured.

Run from my_approach/:
    python webapp/server.py
"""

import json
import os
import queue
import sys
import threading
import time
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.clip_writer import build_store, clips_enabled, write_frames, FrameRetentionBuffer
from src.config import add_class_to_config, load_config, save_calibration
from src.encoder import build_encoder
from src.event_log import (
    SOURCE_LIVE,
    EventLogWriter,
    live_session_id,
    new_run_id,
    read_events,
    utc_now,
)
from src.live import CameraStream, LiveDetector, load_live_pose_extractor
from src.prototypes import bank_path, build_bank, save_bank

CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "config.yaml")

# Uncalibrated demo default. The real threshold comes from scripts/calibrate.py
# (spec 8); this exists only so the "live test" step works before that has
# ever been run. cache/calibration.json's existing value (if any) always wins.
DEMO_TAU_HIGH = 1.5


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

        # browser webcam ingest (None when using server OpenCV camera)
        self.ingest_stream = None

        # shared preview + broadcast
        self.latest_frame_jpeg = None
        self.subscribers = []  # list of queue.Queue, one per connected browser tab

    def publish(self, kind, payload):
        msg = json.dumps({"kind": kind, **payload})
        with self.lock:
            subs = list(self.subscribers)
        for q in subs:
            q.put(msg)


STATE = AppState()
app = FastAPI(title="Few-shot action detector -- demo")


def cfg():
    return load_config(CONFIG_PATH)


def get_encoder(c):
    """Load-once encoder, shared by prototype building and live detection."""
    backbone = c.get("backbone", "vjepa")
    with STATE.lock:
        if STATE.encoder is None or STATE.encoder_backbone != backbone:
            STATE.publish("status", {"message": "loading %s encoder (first time downloads weights)..." % backbone})
            STATE.encoder = build_encoder(c, batch_size=4)
            STATE.encoder_backbone = backbone
        return STATE.encoder


def ensure_calibration(c):
    """Write a preset demo threshold only if calibrate.py has never run."""
    if c.get("tau_high") is not None:
        return float(c["tau_high"]), c.get("_tau_high_source", "config.yaml")
    save_calibration(c, DEMO_TAU_HIGH, extra={"source": "demo-preset (uncalibrated, see spec 8)"})
    return DEMO_TAU_HIGH, "demo-preset"


def _encode_jpeg(rgb_frame):
    import cv2

    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb_frame, cv2.COLOR_RGB2BGR), [int(cv2.IMWRITE_JPEG_QUALITY), 70])
    return buf.tobytes() if ok else None


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
        self._watchdog = threading.Thread(target=self._watch, name="browser-cam", daemon=True)
        self._watchdog.start()
        return self

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
        drop_on_backpressure=cfg_live.get("drop_on_backpressure", True),
        warmup_sec=float(cfg_live.get("camera_warmup_sec", 5.0)),
    ).start()


# --------------------------------------------------------------------------
# Class / bank status
# --------------------------------------------------------------------------

def class_status(c):
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
    classes = class_status(c)
    tau_high = c.get("tau_high")
    return {
        "mode": STATE.mode,
        "classes": classes,
        "bank_built": os.path.exists(bank_path(c)),
        "tau_high": tau_high,
        "tau_high_source": c.get("_tau_high_source"),
        "clips_enabled": clips_enabled(c),
        "backbone": c.get("backbone"),
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


@app.get("/api/preview.jpg")
def api_preview():
    with STATE.lock:
        frame = STATE.latest_frame_jpeg
    if frame is None:
        # 204: idle / camera warming up. Prefer this over 404 so the browser
        # poll does not spam the server log while Record/Live is starting.
        from fastapi.responses import Response

        return Response(status_code=204)
    return StreamingResponse(iter([frame]), media_type="image/jpeg")


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
            jpeg = _encode_jpeg(frame)
            if jpeg is not None:
                with STATE.lock:
                    STATE.latest_frame_jpeg = jpeg
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
            STATE.latest_frame_jpeg = None
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
        os.makedirs(class_dir, exist_ok=True)
        out_path = os.path.join(class_dir, "ref_%s.mp4" % time.strftime("%Y%m%dT%H%M%S"))
        write_frames(out_path, frames, c["working_fps"])
    except Exception:
        # Mode was still "recording" (only the success path below advances it
        # to "building"), so a write failure here would otherwise strand it --
        # every future record/live start then 409s "busy" until a restart.
        with STATE.lock:
            STATE.mode = "idle"
            STATE.latest_frame_jpeg = None
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
        STATE.latest_frame_jpeg = None
    threading.Thread(target=_build_loop, args=(c,), daemon=True).start()

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
    class_dir = c.path("data", "references", class_name)
    try:
        os.makedirs(class_dir, exist_ok=True)
        ext = os.path.splitext(file.filename or "")[1].lower() or ".mp4"
        out_path = os.path.join(class_dir, "ref_%s%s" % (time.strftime("%Y%m%dT%H%M%S"), ext))
        with open(out_path, "wb") as fh:
            fh.write(await file.read())
    except Exception:
        # Mode was already set to "building" above; a write failure here would
        # otherwise strand it, since nothing else resets it back to "idle".
        with STATE.lock:
            STATE.mode = "idle"
        raise

    threading.Thread(target=_build_loop, args=(c,), daemon=True).start()
    return {"ok": True, "path": out_path}


def _build_loop(c):
    """The background work: encode every class's references into the
    prototype bank (spec 6), same as scripts/build_prototypes.py -- including
    the pose/hand template stages when those streams are enabled, so a config
    that turns them on gets templates the live loop can actually load."""
    try:
        if c.get("pose", {}).get("enabled", False):
            STATE.publish("build_progress", {"message": "building pose templates..."})
            from src.pose import build_pose_templates, save_pose_templates

            pose_templates = build_pose_templates(c, verbose=False)
            if pose_templates:
                save_pose_templates(c, pose_templates)

        if c.get("hands", {}).get("enabled", False):
            STATE.publish("build_progress", {"message": "building hand templates..."})
            from src.hands import build_hand_templates, save_hand_templates

            hand_templates = build_hand_templates(c, verbose=False)
            if hand_templates:
                save_hand_templates(c, hand_templates)

        STATE.publish("build_progress", {"message": "building prototype bank..."})
        encoder = get_encoder(c)
        bank, w_base_sec = build_bank(c, encoder, verbose=False)
        save_bank(c, bank, w_base_sec)
        ensure_calibration(c)
        STATE.publish("build_done", {
            "message": "ready for detection",
            "classes": sorted(bank.keys()),
            "w_base_sec": round(w_base_sec, 2),
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

    try:
        encoder = get_encoder(c)
        from src.prototypes import load_bank

        bank, w_base_sec = load_bank(c)
        c = load_config(CONFIG_PATH, w_base_sec=w_base_sec)
        tau_high, tau_source = ensure_calibration(c)
    except (Exception, SystemExit) as exc:  # noqa: BLE001
        # SystemExit, not just Exception: load_bank raises it (missing bank,
        # or a bank file present but containing no classes -- e.g. corrupted
        # or left over from an aborted build). /api/live/start already checks
        # the bank file exists, but not that it's non-empty, so this can still
        # be hit. Uncaught, STATE.mode would stay stuck at "detecting" forever
        # (there is no `finally` here) -- every future record/live start then
        # 409s "busy" until the server is restarted.
        STATE.publish("live_error", {"message": str(exc)})
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
    )

    live_cfg = c["live"]
    if camera_index is None:
        camera_index = live_cfg["camera_index"]
    try:
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
    try:
        pose_templates, pose_extractor = load_live_pose_extractor(c)
    except (FileNotFoundError, OSError, RuntimeError) as exc:
        STATE.publish("live_error", {
            "message": "pose landmarker failed to load (ear_cover wrist gate): %s" % exc,
        })
        stream.release()
        _clear_ingest(stream)
        writer.close()
        with STATE.lock:
            STATE.mode = "idle"
        return
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
        if clips_enabled(c):
            store = build_store(c)
            # Hold enough history for confirm_sec (hair) + pre_roll so clips
            # still get lead-in after a delayed open.
            max_confirm = max(
                (float(c.class_cfg(n).get("confirm_sec", 0.0)) for n in c.class_names),
                default=0.0,
            )
            base_horizon = (
                0.5 * float(c["w_base_sec"])
                + c["smoothing_windows"] * c.stride_chunks * c["chunk_sec"]
                + float(c.get("clips", {}).get("pre_roll_sec", 1.0))
                + max_confirm
                + 4.0
            )
            frame_buffer = FrameRetentionBuffer(
                base_horizon_sec=base_horizon,
                hard_ceiling_sec=store.max_clip_sec + 10.0 + max_confirm,
                jpeg_quality=c["clips"].get("jpeg_quality", 80),
            )

        detector = LiveDetector(
            c, encoder, bank, tau_high, writer, on_event, session_id=session_id,
            pose_templates=pose_templates, hand_templates=hand_templates,
            background_scale=background_scale,
        )
        detector.frame_buffer = frame_buffer
        detector.clip_store = store

        STATE.publish("live_started", {
            "session_id": session_id,
            "classes": detector.class_names,
            "streams": detector.active_streams,
            "tau_high": tau_high,
            "tau_high_source": tau_source,
            "clips_enabled": store is not None,
            "warmup_sec": detector.warmup_chunks * detector.chunk_sec,
            "source": source,
        })

        t0 = time.time()
        frame_interval = 1.0 / float(c["working_fps"])
        next_frame_at = t0
        buffer = []
        chunk_index = 0
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
            buffer.append((capture_sec, frame))
            if frame_buffer is not None:
                frame_buffer.append(capture_sec, frame)
            jpeg = _encode_jpeg(frame)
            if jpeg is not None:
                with STATE.lock:
                    STATE.latest_frame_jpeg = jpeg
            next_frame_at += frame_interval
            if next_frame_at < now:
                next_frame_at = now + frame_interval

            if len(buffer) >= detector.frames_per_chunk:
                taken = buffer[: detector.frames_per_chunk]
                buffer = buffer[detector.frames_per_chunk:]
                start_sec = taken[0][0]
                end_sec = taken[-1][0] + frame_interval

                import numpy as np
                clip = np.stack([f for _, f in taken])
                feature = encoder.encode_clips([clip])[0]
                pose_seq = hand_seq = None
                if pose_extractor is not None:
                    from src.pose import normalize_pose

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
            except Exception:  # noqa: BLE001
                pass
        try:
            writer.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            stream.release()
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
            STATE.latest_frame_jpeg = None
            STATE.mode = "idle"
        STATE.publish("live_stopped", {"session_id": session_id})


@app.post("/api/live/start")
def api_live_start(
    camera_index: Optional[int] = None,
    max_seconds: Optional[float] = None,
    source: str = "browser",
):
    c = cfg()
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
    return {"ok": True, "source": source}


@app.post("/api/live/stop")
def api_live_stop():
    with STATE.lock:
        if STATE.mode != "detecting":
            raise HTTPException(409, "not currently running a live test")
    STATE.live_stop.set()
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
    threading.Thread(target=_build_loop, args=(cfg(),), daemon=True).start()
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
    class_dir = c.path("data", "references", class_name)
    os.makedirs(class_dir, exist_ok=True)
    return {"ok": True, "name": class_name, "path": class_dir}


# --------------------------------------------------------------------------
# Static frontend
# --------------------------------------------------------------------------

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


if __name__ == "__main__":
    import uvicorn

    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host=host, port=port)

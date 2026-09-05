"""Failure-mode wiring tests for the "add a new reference" -> detection path
in webapp/server.py.

Everything here checks one invariant: STATE.mode must never get stranded on a
failure -- every entry into "recording"/"building"/"detecting" must have a
path back to "idle" (with a message published so the page can show it),
however the underlying operation fails. Two failure classes were found not to
hold that invariant:

  * src.prototypes.load_bank and src.pose.build_pose_templates raise
    SystemExit for their fatal errors (idiomatic in the CLI scripts they were
    written for). SystemExit is a BaseException, not an Exception, so it slips
    past `except Exception` in _build_loop/_live_loop -- the background
    thread would die with no build_error/live_error reaching the page.

  * api_record_stop and api_references_upload set STATE.mode BEFORE writing
    the reference clip to disk, and only reset it to "idle" on the success
    path -- a write failure (disk full, bad permissions) left mode stranded,
    409-"busy"-ing every future record/live start until a server restart.
"""

import json
import os
import sys
import threading

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from tests.fixtures import synthetic_clip, write_video  # noqa: E402
from tests.stub_encoder import StubEncoder  # noqa: E402

import webapp.server as server_mod  # noqa: E402

FPS = 8.0

CONFIG_YAML = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
tau_high: 0.5
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
max_false_alarms_per_hour: 5
frames_per_clip: 8
event_log:
  path: data/events/events.csv
pose:
  enabled: true
clips:
  enabled: false
classes:
  wiggle:
    allow_flip: true
"""


class NoPosePoseExtractor:
    """Detects nothing, on every frame -- the real "camera can't see a pose"
    case that drives build_pose_templates to raise SystemExit."""

    def __init__(self, *args, **kwargs):
        pass

    def landmarks_for_clip(self, clip):
        from src.pose import UPPER_BODY

        return np.full((len(clip), len(UPPER_BODY), 3), np.nan, dtype=np.float32)

    def close(self):
        pass


@pytest.fixture()
def workspace(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML, encoding="utf-8")
    (tmp_path / "cache").mkdir()

    ref_dir = tmp_path / "data" / "references" / "wiggle"
    ref_dir.mkdir(parents=True)
    reference = synthetic_clip("oscillate", int(2.0 * FPS), fps=FPS, freq=2.0, seed=99)
    write_video(str(ref_dir / "ref1.mp4"), reference, fps=FPS)

    return str(config_path)


def _drain(queue_obj):
    msgs = []
    while True:
        try:
            msgs.append(json.loads(queue_obj.get_nowait()))
        except Exception:
            break
    return msgs


def test_build_loop_reports_systemexit_and_resets_mode(workspace, monkeypatch):
    """A reference clip with no detectable pose makes the real
    build_pose_templates raise SystemExit. _build_loop must surface it as
    build_error and put STATE.mode back to idle, not die silently."""
    cfg = load_config(workspace)
    monkeypatch.setattr("src.pose.PoseExtractor", NoPosePoseExtractor)
    monkeypatch.setattr(server_mod, "get_encoder", lambda c: StubEncoder(frames_per_clip=8))

    import queue as queue_mod

    q = queue_mod.Queue()
    server_mod.STATE.mode = "building"
    server_mod.STATE.subscribers = [q]

    server_mod._build_loop(cfg)

    assert server_mod.STATE.mode == "idle"
    kinds = [m["kind"] for m in _drain(q)]
    assert "build_error" in kinds


def test_live_loop_reports_systemexit_and_resets_mode(workspace, monkeypatch):
    """load_bank raises SystemExit for a bank file that exists but has no
    classes (e.g. a corrupted/aborted build) -- a case /api/live/start's
    os.path.exists check does not catch. _live_loop must not strand mode at
    "detecting" forever."""
    cfg = load_config(workspace)

    def raise_system_exit(c):
        raise SystemExit("prototype bank at %s contains no classes" % c.path("cache", "x.npz"))

    monkeypatch.setattr("src.prototypes.load_bank", raise_system_exit)
    monkeypatch.setattr(server_mod, "CONFIG_PATH", workspace)
    monkeypatch.setattr(server_mod, "get_encoder", lambda c: StubEncoder(frames_per_clip=8))

    import queue as queue_mod

    q = queue_mod.Queue()
    server_mod.STATE.mode = "detecting"
    server_mod.STATE.subscribers = [q]

    server_mod._live_loop(cfg, camera_index=0, max_seconds=1.0, source="server")

    assert server_mod.STATE.mode == "idle"
    kinds = [m["kind"] for m in _drain(q)]
    assert "live_error" in kinds


def test_record_stop_write_failure_does_not_strand_recording_mode(workspace, monkeypatch):
    """A write_frames failure while saving a recorded clip must not leave
    STATE.mode stuck at "recording" -- that would 409 "busy" on every future
    record/live start until the server is restarted."""
    monkeypatch.setattr(server_mod, "CONFIG_PATH", workspace)

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(server_mod, "write_frames", boom)

    frame = synthetic_clip("static", 1, fps=FPS)[0]
    server_mod.STATE.mode = "recording"
    server_mod.STATE.record_class = "wiggle"
    server_mod.STATE.record_frames = [(0.0, frame)] * 4
    server_mod.STATE.record_thread = None
    server_mod.STATE.record_stop = threading.Event()

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app, raise_server_exceptions=False)
    resp = client.post("/api/record/stop", params={"save": "true"})

    assert resp.status_code == 500
    assert server_mod.STATE.mode == "idle"


def test_upload_write_failure_does_not_strand_building_mode(workspace, monkeypatch):
    """Same invariant for the upload path: a write failure after mode has
    already advanced to "building" must still return to "idle"."""
    monkeypatch.setattr(server_mod, "CONFIG_PATH", workspace)

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr(server_mod.os, "makedirs", boom)
    server_mod.STATE.mode = "idle"

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app, raise_server_exceptions=False)
    resp = client.post(
        "/api/references/upload",
        params={"class_name": "wiggle"},
        files={"file": ("ref.mp4", b"not a real video", "video/mp4")},
    )

    assert resp.status_code == 500
    assert server_mod.STATE.mode == "idle"


def test_api_classes_create_registers_class_in_config_not_just_on_disk(workspace, monkeypatch):
    """The bug this whole file is really about: /api/classes (what the "New
    use case..." UI flow calls) used to only os.makedirs() the reference
    directory. cfg.class_names comes from config.yaml's `classes:` block, not
    from the filesystem, and build_bank/LiveDetector iterate cfg.class_names --
    so a class created this way was recorded into forever, encoded never, and
    detected nowhere. It must now also land in config.yaml."""
    monkeypatch.setattr(server_mod, "CONFIG_PATH", workspace)
    server_mod.STATE.mode = "idle"

    from fastapi.testclient import TestClient

    client = TestClient(server_mod.app, raise_server_exceptions=False)
    resp = client.post("/api/classes", params={"class_name": "head_nodding"})

    assert resp.status_code == 200
    reloaded = load_config(workspace)
    assert "head_nodding" in reloaded.class_names
    assert os.path.isdir(reloaded.path("data", "references", "head_nodding"))

    # Calling it again (re-recording into an already-known class) must not
    # duplicate the config.yaml entry.
    resp2 = client.post("/api/classes", params={"class_name": "head_nodding"})
    assert resp2.status_code == 200
    text = open(workspace, encoding="utf-8").read()
    assert text.count("head_nodding:") == 1

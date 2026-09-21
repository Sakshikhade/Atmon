"""Drawing a skeleton must never change what gets detected.

pose.enabled stays false because DTW fusion measured HARMFUL on held-out eval3
(every fused variant collapsed to mAP 0.125 / 19.3 FA-h against appearance-only
0.750 / 0.0). The overlay wants the same MediaPipe landmarker for rendering, and
the danger is that wiring it up quietly re-enters the scoring path.

The guarantee is structural -- load_live_pose_extractor returns templates=None,
so active_streams never appends "pose" -- but "structural" is a claim, so the
last test here drives the real live loop twice over identical frames, overlay
off and on, and diffs the emitted event rows. config.yaml's `overlay:` comment
points at this file.
"""

import json
import threading

import numpy as np
import pytest

import webapp.server as server_mod
from src.config import load_config
from src.live import load_live_pose_extractor, overlay_enabled, pose_required_for_scoring
from src.pose import UPPER_BODY
from tests.fixtures import synthetic_clip, synthetic_timeline, write_video
from tests.stub_encoder import StubEncoder

FPS = 8.0

BASE_CONFIG = """
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
live:
  background_warmup_chunks: 3
pose:
  enabled: false
hands:
  enabled: false
clips:
  enabled: false
overlay:
  enabled: %s
classes:
  wiggle:
    allow_flip: true
"""


# Background, then action, then background. The golden diff below is worthless
# unless the run actually DETECTS something, and a stream of pure action never
# gets there: RunningBackground rejects a degenerate (near-zero MAD) estimate,
# so the detector never becomes ready and every run trivially emits zero rows.
TIMELINE = [
    ("static", 5.0, None),
    ("oscillate", 4.0, "wiggle"),
    ("static", 3.0, None),
]
RUN_SECONDS = 12.0


class FakeCameraStream:
    """Deterministic frames -- both runs must see byte-identical input."""

    def __init__(self, *args, **kwargs):
        frames, _ = synthetic_timeline(TIMELINE, fps=FPS, seed=1)
        self._frames = list(frames)
        self._i = 0
        self.stopped = False
        self.failure_reason = None

    def start(self):
        return self

    def read(self):
        frame = self._frames[self._i % len(self._frames)]
        self._i += 1
        return frame

    def release(self):
        self.stopped = True


class FakePoseExtractor:
    def __init__(self, *args, **kwargs):
        self.closed = False

    def detect_frame(self, frame):
        rng = np.random.default_rng(int(frame[0, 0, 0]))
        xyz = rng.normal(0.5, 0.02, size=(len(UPPER_BODY), 3)).astype(np.float32)
        return xyz, np.ones(len(UPPER_BODY), dtype=np.float32)

    def landmarks_for_clip(self, clip):
        return np.stack([self.detect_frame(f)[0] for f in clip])

    def close(self):
        self.closed = True


def _workspace(tmp_path, overlay):
    tmp_path.mkdir(parents=True, exist_ok=True)
    config_path = tmp_path / "config.yaml"
    config_path.write_text(BASE_CONFIG % ("true" if overlay else "false"), encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "calibration.json").write_text(json.dumps({"tau_high": 0.5}), encoding="utf-8")
    ref_dir = tmp_path / "data" / "references" / "wiggle"
    ref_dir.mkdir(parents=True)
    write_video(str(ref_dir / "ref1.mp4"),
                synthetic_clip("oscillate", int(2.0 * FPS), fps=FPS, freq=2.0, seed=99),
                fps=FPS)
    return str(config_path)


# -- the structural guarantee ------------------------------------------------

def test_overlay_loads_the_extractor_without_templates(tmp_path, monkeypatch):
    """No wrist gate, pose off, overlay on -> landmarker yes, pose stream no."""
    monkeypatch.setattr("src.pose.PoseExtractor", FakePoseExtractor)
    cfg = load_config(_workspace(tmp_path, overlay=True))

    assert overlay_enabled(cfg)
    templates, extractor = load_live_pose_extractor(cfg)
    assert extractor is not None, "the overlay needs the landmarker"
    assert templates is None, "and must not carry DTW templates into scoring"


def test_overlay_alone_does_not_make_pose_required(tmp_path):
    """A cosmetic dependency must not be able to abort a session."""
    cfg = load_config(_workspace(tmp_path, overlay=True))
    assert pose_required_for_scoring(cfg) is False


def test_wrist_gate_does_make_pose_required(tmp_path):
    path = _workspace(tmp_path, overlay=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("    require_wrist_near_ear: true\n")
    cfg = load_config(path)
    assert pose_required_for_scoring(cfg) is True


# -- the golden diff ---------------------------------------------------------

def _run(tmp_path, overlay, monkeypatch):
    config_path = _workspace(tmp_path, overlay=overlay)
    cfg = load_config(config_path)

    monkeypatch.setattr(server_mod, "CONFIG_PATH", config_path)
    monkeypatch.setattr(server_mod, "get_encoder", lambda c: StubEncoder(frames_per_clip=8))
    monkeypatch.setattr(server_mod, "CameraStream", FakeCameraStream)
    monkeypatch.setattr("src.pose.PoseExtractor", FakePoseExtractor)

    server_mod.STATE.mode = "building"
    server_mod._build_loop(cfg)
    assert server_mod.STATE.mode == "idle"

    streams = {}
    published = []

    class RecordingLiveDetector(server_mod.LiveDetector):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            streams["active"] = list(self.active_streams)
            streams["pose_templates"] = kwargs.get("pose_templates")

    monkeypatch.setattr(server_mod, "LiveDetector", RecordingLiveDetector)
    monkeypatch.setattr(server_mod.STATE, "publish",
                        lambda kind, payload: published.append((kind, payload)))

    server_mod.STATE.mode = "detecting"
    server_mod.STATE.live_stop = threading.Event()
    server_mod.STATE.subscribers = []
    server_mod._live_loop(cfg, camera_index=0, max_seconds=RUN_SECONDS, source="server")

    log = cfg.path(cfg["event_log"]["path"])
    import csv
    import os
    rows = []
    if os.path.exists(log):
        with open(log, newline="", encoding="utf-8") as fh:
            rows = [(r["status"], r["class"], r["start_sec"], r["end_sec"], r["score"])
                    for r in csv.DictReader(fh)]
    return rows, streams, published


@pytest.fixture(scope="module")
def both_runs(tmp_path_factory):
    """One run with the overlay off, one on. Module-scoped: each drives the real
    live loop at real-time pace, so re-running per test would cost ~12s each."""
    from _pytest.monkeypatch import MonkeyPatch

    out = {}
    for name, overlay in (("off", False), ("on", True)):
        mp = MonkeyPatch()
        try:
            base = tmp_path_factory.mktemp("overlay_%s" % name)
            rows, streams, published = _run(base / name, overlay, mp)
            out[name] = {"rows": rows, "streams": streams, "published": published}
        finally:
            mp.undo()
    return out


def test_the_runs_actually_detected_something(both_runs):
    """Guards the golden diff below from being vacuous.

    Two empty logs compare equal and prove nothing. An earlier version of this
    file did exactly that: all-action frames left RunningBackground degenerate
    (near-zero MAD), the detector never became ready, and both runs emitted zero
    rows while the test passed.
    """
    for name in ("off", "on"):
        rows = both_runs[name]["rows"]
        assert rows, "%s run produced no events -- the diff would be vacuous" % name
        assert any(r[0] == "closed" for r in rows), "%s run never closed one" % name


def test_overlay_never_enters_the_scoring_path(both_runs):
    """active_streams stays appearance-only with the overlay on."""
    assert both_runs["on"]["streams"]["active"] == ["vjepa"]
    assert both_runs["on"]["streams"]["pose_templates"] is None


def test_overlay_publishes_pose_frames(both_runs):
    published = both_runs["on"]["published"]
    assert "pose_frame" in [k for k, _ in published], "overlay on must emit landmarks"

    started = [p for k, p in published if k == "live_started"][0]
    assert started["overlay"]["enabled"] is True
    assert started["overlay"]["edges"], "topology must ship from the server"

    frame = [p for k, p in published if k == "pose_frame"][0]
    assert len(frame["points"]) == len(UPPER_BODY)
    assert "t" in frame and "seq" in frame


def test_overlay_off_publishes_nothing(both_runs):
    assert "pose_frame" not in [k for k, _ in both_runs["off"]["published"]]


def test_detections_are_identical_with_and_without_the_overlay(both_runs):
    """THE test. Same frames, same encoder, same seed -- same detections.

    Compared on status / class / SCORE, exactly. The score is what proves the
    scoring path was untouched: if rendering ever leaked into it, the fused
    value moves.

    start_sec/end_sec are deliberately compared with tolerance instead. They are
    derived from measured capture time (spec 10.3 -- never a chunk counter), so
    the overlay's extra per-frame work shifts them by milliseconds of real
    wall-clock. That is the timestamps being honest about when frames arrived,
    not scoring drifting; asserting equality there is a flaky test, not a
    stronger one.
    """
    off_rows = both_runs["off"]["rows"]
    on_rows = both_runs["on"]["rows"]

    assert len(on_rows) == len(off_rows)
    for off, on in zip(off_rows, on_rows):
        off_status, off_cls, off_start, off_end, off_score = off
        on_status, on_cls, on_start, on_end, on_score = on
        assert (on_status, on_cls) == (off_status, off_cls)
        assert on_score == off_score, "scores must be bit-identical"
        for a, b in ((off_start, on_start), (off_end, on_end)):
            if a == "" or b == "":
                assert a == b
            else:
                assert float(a) == pytest.approx(float(b), abs=0.25)


# -- backpressure ------------------------------------------------------------

def test_pose_frames_are_dropped_when_a_tab_falls_behind():
    """~8/sec into an unbounded queue is a leak. Detections are never dropped."""
    import queue

    state = server_mod.AppState()
    q = queue.Queue()
    for _ in range(server_mod.POSE_QUEUE_MAX + 1):
        q.put("backlog")
    state.subscribers = [q]

    before = q.qsize()
    state.publish("pose_frame", {"points": None})
    assert q.qsize() == before, "a backed-up subscriber must drop pose_frame"

    state.publish("live_open", {"class": "wiggle"})
    assert q.qsize() == before + 1, "real events must never be dropped"

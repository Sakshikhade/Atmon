"""Wiring test for webapp/server.py against src/live.py and scripts/build_prototypes.py.

`_build_loop` and `_live_loop` re-implement, for the web demo, what
scripts/build_prototypes.py and src.live.run_live do for the CLI (the live loop
cannot call run_live directly -- that installs a main-thread-only SIGINT
handler). They are supposed to match: pose/hand templates built when those
streams are enabled and loaded back into LiveDetector, and a calibrated
background_scale read from cache/calibration.json when live.use_calibrated_scale
is set. All three were previously skipped or hardcoded away in the web path,
which silently dropped pose/hands and the calibrated scale from the demo
regardless of config.yaml. This test builds templates through _build_loop, then
runs _live_loop, and checks the real (unmocked) save/load code carries them
through to LiveDetector and push_chunk.
"""

import json
import threading

import numpy as np
import pytest

from src.config import load_config  # noqa: E402
from tests.fixtures import synthetic_clip, write_video  # noqa: E402
from tests.stub_encoder import StubEncoder  # noqa: E402

import webapp.server as server_mod  # noqa: E402

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
live:
  background_warmup_chunks: 50
  use_calibrated_scale: true
pose:
  enabled: true
hands:
  enabled: true
clips:
  enabled: false
classes:
  wiggle:
    allow_flip: true
"""

FPS = 8.0

class FakeCameraStream:
    """Stand-in for src.live.CameraStream: hands back synthetic frames on demand."""

    def __init__(self, *args, **kwargs):
        clip = synthetic_clip("oscillate", 64, fps=FPS, freq=2.0, seed=1)
        self._frames = list(clip)
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

def _landmarks(n_frames, n_points, wrist_idx, ref_idx):
    """A plausible, NaN-free landmark sequence: distinct wrist/reference points,
    everything else lightly jittered so normalize_pose/normalize_hand and their
    DTW similarity run their real numeric path instead of short-circuiting on
    NaN (a real MediaPipe model isn't available in this environment)."""
    rng = np.random.default_rng(0)
    seq = rng.normal(0, 0.02, size=(n_frames, n_points, 3)).astype(np.float32)
    seq[:, wrist_idx] += (0.5, 0.5, 0.0)
    seq[:, ref_idx] += (0.6, 0.3, 0.0)
    return seq

class FakePoseExtractor:
    def __init__(self, *args, **kwargs):
        self.closed = False

    def landmarks_for_clip(self, clip):
        from src.pose import L_SHOULDER, R_SHOULDER, UPPER_BODY

        return _landmarks(len(clip), len(UPPER_BODY), L_SHOULDER, R_SHOULDER)

    def close(self):
        self.closed = True

class FakeHandExtractor:
    def __init__(self, *args, **kwargs):
        self.closed = False

    def landmarks_for_clip(self, clip):
        from src.hands import MIDDLE_MCP, N_HAND_LANDMARKS, WRIST

        return _landmarks(len(clip), N_HAND_LANDMARKS, WRIST, MIDDLE_MCP)

    def close(self):
        self.closed = True

@pytest.fixture()
def workspace(tmp_path):
    config_path = tmp_path / "config.yaml"
    config_path.write_text(CONFIG_YAML, encoding="utf-8")

    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    (cache_dir / "calibration.json").write_text(
        json.dumps({"tau_high": 0.5, "background_scale": {"wiggle": 0.031}}),
        encoding="utf-8",
    )

    ref_dir = tmp_path / "data" / "references" / "wiggle"
    ref_dir.mkdir(parents=True)
    reference = synthetic_clip("oscillate", int(2.0 * FPS), fps=FPS, freq=2.0, seed=99)
    write_video(str(ref_dir / "ref1.mp4"), reference, fps=FPS)

    return str(config_path)

def test_build_then_live_wires_pose_hands_and_calibrated_scale(workspace, monkeypatch):
    cfg = load_config(workspace)

    monkeypatch.setattr(server_mod, "CONFIG_PATH", workspace)
    monkeypatch.setattr(server_mod, "get_encoder", lambda c: StubEncoder(frames_per_clip=8))
    monkeypatch.setattr(server_mod, "CameraStream", FakeCameraStream)
    monkeypatch.setattr("src.pose.PoseExtractor", FakePoseExtractor)
    monkeypatch.setattr("src.hands.HandExtractor", FakeHandExtractor)

    # -- Step 1: build, through the web demo's real code path -----------------
    server_mod.STATE.mode = "building"
    server_mod._build_loop(cfg)
    assert server_mod.STATE.mode == "idle"

    from src.pose import load_pose_templates
    from src.hands import load_hand_templates
    from src.prototypes import load_bank

    pose_templates = load_pose_templates(cfg)
    hand_templates = load_hand_templates(cfg)
    assert pose_templates and "wiggle" in pose_templates
    assert hand_templates and "wiggle" in hand_templates
    bank, _ = load_bank(cfg)
    assert "wiggle" in bank

    # -- Step 2: live, through the web demo's real code path ------------------
    captured = {}
    chunks = []

    class RecordingLiveDetector(server_mod.LiveDetector):
        def __init__(self, *args, **kwargs):
            captured["pose_templates"] = kwargs.get("pose_templates")
            captured["hand_templates"] = kwargs.get("hand_templates")
            captured["background_scale"] = kwargs.get("background_scale")
            super().__init__(*args, **kwargs)
            captured["active_streams"] = list(self.active_streams)

        def push_chunk(self, feature, start_sec, end_sec=None, pose_seq=None, hand_seq=None):
            chunks.append({"pose_seq": pose_seq, "hand_seq": hand_seq})
            return super().push_chunk(feature, start_sec, end_sec, pose_seq, hand_seq)

    monkeypatch.setattr(server_mod, "LiveDetector", RecordingLiveDetector)

    server_mod.STATE.mode = "detecting"
    server_mod.STATE.live_stop = threading.Event()
    server_mod.STATE.subscribers = []

    server_mod._live_loop(cfg, camera_index=0, max_seconds=1.3, source="server")

    # Gap 1: the calibrated background_scale from cache/calibration.json must
    # reach LiveDetector, not be dropped as None.
    assert captured["background_scale"] == {"wiggle": 0.031}

    # Gap 2: pose/hand templates -- built in step 1 through the demo's own
    # build path, then loaded back by the real (unmocked) loaders -- must reach
    # LiveDetector, and both streams must be active.
    assert captured["pose_templates"] is not None and "wiggle" in captured["pose_templates"]
    assert captured["hand_templates"] is not None and "wiggle" in captured["hand_templates"]
    assert set(captured["active_streams"]) == {"vjepa", "pose", "hands"}

    # And each pushed chunk actually carried extracted pose/hand sequences
    # through to push_chunk, not None.
    assert chunks, "expected at least one chunk to have been pushed"
    assert all(c["pose_seq"] is not None for c in chunks)
    assert all(c["hand_seq"] is not None for c in chunks)

    assert server_mod.STATE.mode == "idle"

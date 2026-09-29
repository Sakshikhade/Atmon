"""The wrist-near-ear gate must be evaluated per class, not once per chunk.

LiveDetector used to read wrist_near_ear_threshold from whichever class came
first out of a set -- non-deterministic iteration order -- and apply that single
threshold, and a single boolean, to every gated class. Correct only while
ear_cover was the only gated class; silently wrong the moment a second one
exists with a different threshold.

These drive LiveDetector directly with a hand-built normalized pose sequence, so
nothing here depends on MediaPipe or on a real encoder.
"""

import os
import tempfile

import numpy as np
import pytest

from src.config import load_config
from src.event_log import (
    SOURCE_LIVE,
    EventLogWriter,
    live_session_id,
    new_run_id,
    utc_now,
)
from src.live import LiveDetector
from src.pose import UPPER_BODY, wrist_near_ear
from tests.stub_encoder import StubEncoder

K = len(UPPER_BODY)
L_SHOULDER, R_SHOULDER = 11, 12
L_EAR, R_EAR = 7, 8
L_WRIST, R_WRIST = 15, 16

# Two gated classes, deliberately different thresholds. The wrist sits ~0.07
# from the ear below, so it clears `loose` (0.28) and fails `tight` (0.05).
CONFIG_YAML = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [1.0]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 1
tau_high: null
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
max_false_alarms_per_hour: 5
frames_per_clip: 16
event_log:
  path: data/events/events.csv
  max_open_sec: 300
live:
  background_warmup_chunks: 3
classes:
  loose:
    allow_flip: false
    require_wrist_near_ear: true
    wrist_near_ear_threshold: 0.28
  tight:
    allow_flip: false
    require_wrist_near_ear: true
    wrist_near_ear_threshold: 0.05
  ungated:
    allow_flip: false
"""


def _pose_wrist_at(distance):
    """Normalized pose with the left wrist `distance` from the left ear."""
    seq = np.full((8, K, 3), np.nan, dtype=np.float32)
    for t in range(len(seq)):
        seq[t, L_SHOULDER] = (-0.5, 0.0, 0.0)
        seq[t, R_SHOULDER] = (0.5, 0.0, 0.0)
        seq[t, L_EAR] = (-0.4, -0.6, 0.0)
        seq[t, R_EAR] = (0.4, -0.6, 0.0)
        seq[t, L_WRIST] = (-0.4 + distance, -0.6, 0.0)
        seq[t, R_WRIST] = (0.5, 0.8, 0.0)  # far from either ear
    return seq


@pytest.fixture()
def workspace():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CONFIG_YAML)
        yield d


@pytest.fixture()
def cfg(workspace):
    return load_config(os.path.join(workspace, "config.yaml"), w_base_sec=1.0)


@pytest.fixture()
def detector(cfg, workspace):
    encoder = StubEncoder(frames_per_clip=16)
    rng = np.random.default_rng(0)
    bank = {
        name: np.asarray(rng.normal(size=(4, encoder.dim)), dtype=np.float32)
        for name in cfg.class_names
    }
    writer = EventLogWriter(
        path=os.path.join(workspace, "live.csv"),
        run_id=new_run_id(),
        source_id=live_session_id(),
        source_type=SOURCE_LIVE,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=1.0,
        source_start_utc=utc_now(),
    )
    det = LiveDetector(cfg, encoder, bank, 1.0, writer)
    yield det
    writer.close()


def test_each_gated_class_keeps_its_own_threshold(detector):
    """The thresholds map must carry per-class values, not one shared number."""
    assert detector.wrist_gate_thresholds == {"loose": 0.28, "tight": 0.05}
    assert "ungated" not in detector.wrist_gate_thresholds


def test_gate_resolves_per_class_on_the_same_pose(detector):
    """One wrist position, two verdicts -- this is what the old code could not do.

    The wrist sits 0.07 from the ear: inside `loose`'s 0.28 and outside
    `tight`'s 0.05. A single shared flag had to report one answer for both.
    """
    pose = _pose_wrist_at(0.07)

    # Sanity: the underlying predicate really does split at these thresholds.
    assert wrist_near_ear(pose, threshold=0.28) is True
    assert wrist_near_ear(pose, threshold=0.05) is False

    feature = np.zeros(detector.encoder.dim, dtype=np.float32)
    detector.push_chunk(feature, 0.0, 1.0, pose_seq=pose)

    assert detector._last_wrist_near_ear["loose"] is True
    assert detector._last_wrist_near_ear["tight"] is False


def test_gate_is_empty_without_pose(detector):
    """No pose sequence means no class may claim geometric confirmation."""
    feature = np.zeros(detector.encoder.dim, dtype=np.float32)
    detector.push_chunk(feature, 0.0, 1.0, pose_seq=None)

    assert detector._last_wrist_near_ear == {"loose": False, "tight": False}


def test_ungated_classes_are_unaffected(cfg, detector):
    """A class without require_wrist_near_ear must never consult the gate."""
    pose = _pose_wrist_at(5.0)  # wrist nowhere near an ear
    feature = np.zeros(detector.encoder.dim, dtype=np.float32)
    detector.push_chunk(feature, 0.0, 1.0, pose_seq=pose)

    # Both gated classes are blocked...
    assert detector._last_wrist_near_ear == {"loose": False, "tight": False}
    # ...and the ungated one was never given an entry to be blocked by.
    assert "ungated" not in detector._last_wrist_near_ear

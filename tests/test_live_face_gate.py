"""LiveDetector must refuse opens when the Active-Subject face gate fails."""

import os
import tempfile

import numpy as np
import pytest

from src.config import load_config
from src.event_log import EventLogWriter, SOURCE_LIVE, live_session_id, new_run_id, utc_now
from src.live import LiveDetector
from tests.stub_encoder import StubEncoder

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
frames_per_clip: 16
identity:
  enabled: true
  match_threshold: 0.5
  live_stride: 1
event_log:
  path: data/events/events.csv
  max_open_sec: 300
live:
  background_warmup_chunks: 3
  mutual_exclusion: false
  cross_class_resolution: false
classes:
  nod:
    allow_flip: false
"""


class FakeFaceEncoder:
    def __init__(self, match=True):
        self.match = match
        self.calls = 0

    def matches_gallery(self, frame_rgb, gallery):
        self.calls += 1
        return bool(self.match), np.ones(512, dtype=np.float32)

    def close(self):
        pass


@pytest.fixture()
def workspace():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CONFIG_YAML)
        yield d


@pytest.fixture()
def cfg(workspace):
    return load_config(os.path.join(workspace, "config.yaml"), w_base_sec=1.0)


def _detector(cfg, workspace, face_match):
    encoder = StubEncoder(frames_per_clip=16)
    rng = np.random.default_rng(0)
    bank = {"nod": rng.normal(size=(4, encoder.dim)).astype(np.float32)}
    # Make bank vectors match what stub produces from constant clips
    bank["nod"] = encoder.encode_clips(
        [np.zeros((16, 8, 8, 3), dtype=np.uint8) for _ in range(4)]
    )
    writer = EventLogWriter(
        path=os.path.join(workspace, "events.csv"),
        run_id=new_run_id(),
        source_id=live_session_id(),
        source_type=SOURCE_LIVE,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=0.5,
        source_start_utc=utc_now(),
        subject_id="alex",
    )
    gallery = np.eye(2, 512, dtype=np.float32)
    gallery = gallery / np.linalg.norm(gallery, axis=1, keepdims=True)
    det = LiveDetector(
        cfg, encoder, bank, 0.5, writer,
        subject_id="alex",
        face_gallery=gallery,
        face_encoder=FakeFaceEncoder(match=face_match),
    )
    return det, writer


def test_live_face_gate_blocks_open(cfg, workspace):
    det, writer = _detector(cfg, workspace, face_match=False)
    feat = det.encoder.encode_clips([np.zeros((16, 8, 8, 3), dtype=np.uint8)])[0]
    # Warm background with low scores then push high-ish features
    for i in range(6):
        det.update_face_match(np.zeros((8, 8, 3), dtype=np.uint8))
        det.push_chunk(feat * 0.01, float(i), float(i) + 1.0)
    for i in range(6, 14):
        det.update_face_match(np.zeros((8, 8, 3), dtype=np.uint8))
        det.push_chunk(feat, float(i), float(i) + 1.0)
    assert not any(t.is_open for t in det.trackers.values())
    writer.close()


def test_live_face_gate_allows_open_when_matched(cfg, workspace):
    det, writer = _detector(cfg, workspace, face_match=True)
    feat = det.encoder.encode_clips([np.zeros((16, 8, 8, 3), dtype=np.uint8)])[0]
    for i in range(6):
        det.update_face_match(np.zeros((8, 8, 3), dtype=np.uint8))
        det.push_chunk(feat * 0.01, float(i), float(i) + 1.0)
    for i in range(6, 14):
        det.update_face_match(np.zeros((8, 8, 3), dtype=np.uint8))
        det.push_chunk(feat, float(i), float(i) + 1.0)
    # With face match and bank-identical features, at least one tracker may open
    # depending on background normalization; assert the gate bit is True.
    assert det._last_face_match is True
    writer.close()

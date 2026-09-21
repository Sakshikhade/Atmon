"""The CLI and web live paths must retain the same amount of pre-roll.

webapp/server.py deliberately re-implements src.live.run_live (run_live installs
a main-thread-only SIGINT handler), and the two had each built the frame-buffer
horizon by hand. They had drifted: +4.0 vs +2.0 of margin, and
`max_clip_sec + 10.0` vs the full lag expression for the ceiling. A clip saved
from the browser therefore had different lead-in than the same detection from
the CLI.

Both now read LiveDetector.retention_horizon_sec / retention_ceiling_sec.
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
from tests.stub_encoder import StubEncoder

CONFIG_YAML = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 2.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
tau_high: 1.5
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
max_false_alarms_per_hour: 5
frames_per_clip: 8
clips:
  enabled: true
  dir: data/clips
  pre_roll_sec: 1.0
  post_roll_sec: 2.0
  max_clip_sec: 60
  max_total_gb: 5.0
live:
  background_warmup_chunks: 5
  smoothing_windows: 1
classes:
  ear_cover:
    allow_flip: true
  hair_twirling:
    allow_flip: true
    confirm_sec: 3.0
"""


@pytest.fixture()
def workspace():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CONFIG_YAML)
        yield d


@pytest.fixture()
def cfg(workspace):
    return load_config(os.path.join(workspace, "config.yaml"), w_base_sec=3.0)


def _detector(cfg, workspace):
    encoder = StubEncoder(frames_per_clip=8)
    rng = np.random.default_rng(0)
    bank = {n: np.asarray(rng.normal(size=(4, encoder.dim)), dtype=np.float32)
            for n in cfg.class_names}
    writer = EventLogWriter(
        path=os.path.join(workspace, "live.csv"),
        run_id=new_run_id(), source_id=live_session_id(),
        source_type=SOURCE_LIVE, model_id=cfg["model_id"],
        working_fps=cfg["working_fps"], chunk_sec=cfg["chunk_sec"],
        tau_high=1.5, source_start_utc=utc_now(),
    )
    return LiveDetector(cfg, encoder, bank, 1.5, writer), writer


def test_horizon_covers_lag_preroll_and_confirm(cfg, workspace):
    """A clip must still get lead-in after the slowest class's confirm hold."""
    det, writer = _detector(cfg, workspace)
    try:
        assert det.max_confirm_sec == 3.0, "hair_twirling's confirm_sec drives this"
        expected = det.lag_sec + det.pre_roll + det.max_confirm_sec + 2.0
        assert det.retention_horizon_sec == pytest.approx(expected)
        # It must actually exceed the delay before a confirmed open is known.
        assert det.retention_horizon_sec > det.lag_sec + det.max_confirm_sec
    finally:
        writer.close()


def test_ceiling_falls_back_without_a_store(cfg, workspace):
    """No clip store means no max_clip_sec to anchor the cap on."""
    det, writer = _detector(cfg, workspace)
    try:
        assert det.clip_store is None
        assert det.retention_ceiling_sec == det.retention_horizon_sec
    finally:
        writer.close()


def test_ceiling_uses_the_store(cfg, workspace):
    from src.clip_writer import build_store

    det, writer = _detector(cfg, workspace)
    try:
        det.clip_store = build_store(cfg)
        expected = (det.clip_store.max_clip_sec + det.pre_roll + det.post_roll
                    + det.lag_sec + det.max_confirm_sec)
        assert det.retention_ceiling_sec == pytest.approx(expected)
        assert det.retention_ceiling_sec > det.retention_horizon_sec
    finally:
        writer.close()


def test_both_live_paths_derive_the_horizon_identically():
    """Neither path may compute the buffer size itself.

    This is the actual regression guard: the numbers agree because there is one
    expression, not because two hand-written ones happen to match today.
    """
    import inspect

    import webapp.server as server
    import src.live as live

    web = inspect.getsource(server._live_loop)
    cli = inspect.getsource(live.run_live)

    for name, src in (("_live_loop", web), ("run_live", cli)):
        assert "retention_horizon_sec" in src, \
            "%s must size the buffer from the detector" % name
        assert "retention_ceiling_sec" in src, \
            "%s must cap the buffer from the detector" % name
        assert "base_horizon_sec=" in src
        # The old hand-rolled arithmetic must be gone from both.
        assert "smoothing_windows" not in src, \
            "%s is re-deriving lag from config again" % name

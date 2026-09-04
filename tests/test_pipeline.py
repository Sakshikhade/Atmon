"""End-to-end tests over synthetic fixtures and the stub encoder.

These exercise the real prototype/scoring/grouping/event-log code paths -- only
the encoder is swapped. They verify PLUMBING, and say nothing about how V-JEPA 2
will score real behaviour.

Covers the spec's own acceptance tests:
  Phase 2 (spec 6)  -- a reference scored against its own bank returns ~1.0
  Phase 3 (spec 7)  -- a known instance is recovered with tIoU >= 0.5
  9   -- offline writes closed rows only; live writes open then closed
  10  -- the live path detects the same instance the offline path does
"""

import collections
import os
import sys
import tempfile

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.event_log import (  # noqa: E402
    SOURCE_LIVE,
    SOURCE_VIDEO,
    EventLogWriter,
    live_session_id,
    new_run_id,
    read_events,
    utc_now,
)
from src.live import LiveDetector  # noqa: E402
from src.prototypes import build_variants, self_similarity  # noqa: E402
from src.scoring import group_detections, score_grid, tiou  # noqa: E402
from tests.fixtures import encode_timeline, synthetic_clip, synthetic_timeline  # noqa: E402
from tests.stub_encoder import StubEncoder  # noqa: E402

FPS = 8.0
CHUNK_SEC = 1.0
REFERENCE_SEC = 2.0

CONFIG_YAML = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
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
  # Shorter than the shipped 15 because the fixtures are short, but not so
  # short that the running background estimate is built from a handful of
  # samples -- that produces unstable sigma and spurious early detections.
  background_warmup_chunks: 10
classes:
  wiggle:
    allow_flip: true
"""


@pytest.fixture()
def workspace():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(CONFIG_YAML)
        yield d


@pytest.fixture()
def cfg(workspace):
    return load_config(os.path.join(workspace, "config.yaml"), w_base_sec=REFERENCE_SEC)


@pytest.fixture()
def encoder():
    return StubEncoder(frames_per_clip=16)


@pytest.fixture()
def bank(cfg, encoder):
    """A prototype bank built from one reference clip, the real Phase 2 way."""
    reference = synthetic_clip("oscillate", int(REFERENCE_SEC * FPS), fps=FPS, freq=2.0, seed=99)
    variants = build_variants(
        reference, cfg.class_cfg("wiggle"), cfg["prototypes"], np.random.default_rng(0)
    )
    return {"wiggle": encoder.encode_clips([v for _, v in variants])}


@pytest.fixture()
def live_timeline():
    """Longer lead-in so the running background can warm up before the action.

    Live standardizes against a running estimate rather than the whole video, so
    it needs background BEFORE the first event -- which is also true of a real
    session. Action at 20.0s -> 28.0s.
    """
    frames, events = synthetic_timeline(
        [("static", 20.0, None), ("oscillate", 8.0, "wiggle"), ("static", 12.0, None)],
        fps=FPS,
        seed=7,
    )
    return frames, events


@pytest.fixture()
def timeline():
    """Background, then a 6s action, then background. Action at 6.0s -> 12.0s."""
    frames, events = synthetic_timeline(
        [("static", 6.0, None), ("oscillate", 6.0, "wiggle"), ("static", 6.0, None)],
        fps=FPS,
        seed=7,
    )
    return frames, events


# -- the stub itself must be discriminative enough to test with --------------


def test_stub_separates_motion_kinds(encoder):
    """If this fails the fixtures are broken, not the pipeline."""
    osc = encoder.encode_clips([synthetic_clip("oscillate", 16, fps=FPS, freq=2.0, seed=1)])[0]
    osc2 = encoder.encode_clips([synthetic_clip("oscillate", 16, fps=FPS, freq=2.0, seed=2)])[0]
    static = encoder.encode_clips([synthetic_clip("static", 16, fps=FPS, seed=3)])[0]
    drift = encoder.encode_clips([synthetic_clip("drift", 16, fps=FPS, seed=4)])[0]

    assert float(osc @ osc2) > float(osc @ static)
    assert float(osc @ osc2) > float(osc @ drift)


def test_clips_at_or_above_the_resample_boundary_embed_alike(encoder):
    """Above frames_per_clip/working_fps, duration stops mattering much.

    This is the regime the pipeline actually runs in: every target chunk holds
    exactly frames_per_clip real frames.
    """
    boundary_frames = encoder.frames_per_clip                    # 16 frames = 2.0s at 8fps
    at = encoder.encode_clips([synthetic_clip("oscillate", boundary_frames, fps=FPS, freq=2.0, seed=1)])[0]
    above = encoder.encode_clips([synthetic_clip("oscillate", boundary_frames * 2, fps=FPS, freq=2.0, seed=1)])[0]
    assert float(at @ above) > 0.9


def test_below_the_resample_boundary_is_a_different_regime(encoder):
    """Frame-repeated clips embed differently, and that is worth knowing.

    Not a defect to fix but a constraint to respect: a reference clip shorter
    than frames_per_clip/working_fps is padded with repeats while every target
    chunk is not, so similarity drops for reasons unrelated to the action.
    build_prototypes.py warns when a reference falls here.
    """
    boundary_frames = encoder.frames_per_clip
    below = encoder.encode_clips([synthetic_clip("oscillate", boundary_frames // 2, fps=FPS, freq=2.0, seed=1)])[0]
    at = encoder.encode_clips([synthetic_clip("oscillate", boundary_frames, fps=FPS, freq=2.0, seed=1)])[0]
    assert float(below @ at) < 0.9, "if this ever passes, the boundary effect is gone -- update the docs"


# -- Phase 2 acceptance (spec 6) -------------------------------------------


def test_reference_scores_near_one_against_its_own_bank(bank):
    """Divergent encoding paths show up here first (spec 6, pitfall 14.1)."""
    assert self_similarity(bank, "wiggle") > 0.9


def test_variants_are_kept_separately_not_averaged(bank):
    assert len(bank["wiggle"]) >= 10, "spec 4: store all variants, never a class mean"


def test_flip_is_gated_per_class(cfg):
    clip = synthetic_clip("oscillate", 16, fps=FPS, seed=5)
    rng = np.random.default_rng(0)
    with_flip = build_variants(clip, {"allow_flip": True}, cfg["prototypes"], rng)
    without = build_variants(clip, {"allow_flip": False}, cfg["prototypes"], np.random.default_rng(0))
    assert any(name.startswith("flip") for name, _ in with_flip)
    assert not any(name.startswith("flip") for name, _ in without)


# -- Phase 3 acceptance (spec 7) -------------------------------------------


def test_detects_known_instance_with_tiou_at_least_half(cfg, encoder, bank, timeline):
    frames, events = timeline
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)
    class_names, grid = score_grid(feats, bank, cfg)

    # Threshold placed between the background and action score modes.
    tau_high = float(np.percentile(grid, 70))
    detections = group_detections(class_names, grid, starts, cfg, tau_high, CHUNK_SEC)

    assert detections, "no detection produced for a clearly present action"
    truth = (events[0]["start"], events[0]["end"])
    best = max(tiou((d["start"], d["end"]), truth) for d in detections)
    assert best >= 0.5, "spec 7 acceptance: detection must cover the instance at tIoU >= 0.5"


def test_action_scores_above_background(cfg, encoder, bank, timeline):
    frames, _ = timeline
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)
    _, grid = score_grid(feats, bank, cfg)

    action = grid[0, 7:11].mean()      # inside 6.0s -> 12.0s
    background = np.concatenate([grid[0, 0:5], grid[0, 13:]]).mean()
    assert action > background + 0.05, (
        "background and action are not separable; with the real encoder this is "
        "the pitfall 14.8 signal to move to Phase 5"
    )


def test_pure_background_produces_no_detections(cfg, encoder, bank):
    frames, _ = synthetic_timeline([("static", 18.0, None)], fps=FPS, seed=3)
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)
    class_names, grid = score_grid(feats, bank, cfg)
    # A threshold calibrated on data with real actions in it.
    detections = group_detections(class_names, grid, starts, cfg, tau_high=0.95)
    assert detections == []


# -- 9: the offline path writes the log ------------------------------------


def test_offline_run_logs_closed_rows_only(cfg, encoder, bank, timeline, workspace):
    frames, _ = timeline
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)
    class_names, grid = score_grid(feats, bank, cfg)
    tau_high = float(np.percentile(grid, 70))
    detections = group_detections(class_names, grid, starts, cfg, tau_high, CHUNK_SEC)

    log_path = os.path.join(workspace, "events.csv")
    with EventLogWriter(
        path=log_path,
        run_id=new_run_id(),
        source_id="synthetic_01",
        source_type=SOURCE_VIDEO,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=tau_high,
        source_start_utc=None,
    ) as writer:
        for det in detections:
            writer.write_closed(det["class"], det["start"], det["end"], det["score"], det["event_id"])

    logged = read_events(log_path)
    assert len(logged) == len(detections)
    assert all(e["status"] == "closed" for e in logged)
    assert all(e["end_sec"] is not None for e in logged)
    assert all(e["start_utc"] is None for e in logged), "no origin given -> no fabricated datetime"


# -- 10: the live path ------------------------------------------------------


def test_live_path_writes_open_then_closed(cfg, encoder, bank, live_timeline, workspace):
    """Drive LiveDetector directly, no camera -- same code the camera feeds."""
    frames, events = live_timeline
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)

    _, grid = score_grid(feats, bank, cfg)
    tau_high = float(np.percentile(grid, 70))

    log_path = os.path.join(workspace, "live.csv")
    writer = EventLogWriter(
        path=log_path,
        run_id=new_run_id(),
        source_id=live_session_id(),
        source_type=SOURCE_LIVE,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=tau_high,
        source_start_utc=utc_now(),
    )
    detector = LiveDetector(cfg, encoder, bank, tau_high, writer)
    for feature, start in zip(feats, starts):
        detector.push_chunk(feature, float(start))
    detector.flush(float(starts[-1]) + CHUNK_SEC)
    writer.close()

    import csv

    with open(log_path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))

    assert rows, "live run produced no rows for a clearly present action"
    assert rows[0]["status"] == "open", "a live detection must announce itself before it ends"
    assert rows[0]["end_sec"] == ""

    collapsed = read_events(log_path)
    closed = [e for e in collapsed if e["status"] == "closed"]
    assert closed, "the detection never closed"
    assert all(e["start_utc"] is not None for e in collapsed), "live always knows its origin"

    truth = (events[0]["start"], events[0]["end"])
    assert max(tiou((e["start_sec"], e["end_sec"]), truth) for e in closed) >= 0.5


def test_live_compensates_the_causal_smoothing_delay(cfg, encoder, bank, live_timeline, workspace):
    """The compensation must actually shift timestamps earlier by the delay.

    Offline smooths with a centered window; live can only look backwards, and a
    trailing mean of N lags the centered one by (N-1)//2 samples. Uncompensated,
    every live event lands that much LATE -- a timestamp error, not a reporting
    delay.

    This drives the detector twice over identical input, once with compensation
    disabled, and compares the two directly. Comparing against the OFFLINE start
    would not isolate this: offline standardizes against the whole video while
    live uses a running estimate, and that difference alone moves boundaries.
    """
    frames, _ = live_timeline
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)
    _, grid = score_grid(feats, bank, cfg)
    tau_high = float(np.percentile(grid, 70))

    def run(compensate):
        path = os.path.join(workspace, "delay_%s.csv" % compensate)
        writer = EventLogWriter(
            path=path, run_id=new_run_id(), source_id=live_session_id(),
            source_type=SOURCE_LIVE, model_id=cfg["model_id"],
            working_fps=cfg["working_fps"], chunk_sec=cfg["chunk_sec"],
            tau_high=tau_high, source_start_utc=utc_now(),
        )
        detector = LiveDetector(cfg, encoder, bank, tau_high, writer)
        if not compensate:
            detector.smoothing_delay_chunks = 0
            detector.span_history = collections.deque(maxlen=1)
        for feature, start in zip(feats, starts):
            detector.push_chunk(feature, float(start))
        detector.flush(float(starts[-1]) + CHUNK_SEC)
        writer.close()
        closed = [e for e in read_events(path) if e["status"] == "closed"]
        assert closed, "no detection with compensate=%s" % compensate
        return detector, closed[0]

    detector, compensated = run(True)
    _, uncompensated = run(False)

    assert detector.smoothing_delay_chunks == 1, "3-wide trailing mean lags by one chunk"
    shift = uncompensated["start_sec"] - compensated["start_sec"]
    assert shift == pytest.approx(detector.smoothing_delay_chunks * CHUNK_SEC), (
        "compensation should move the start exactly one chunk earlier, got %.1fs" % shift
    )


def test_live_reports_its_lag(cfg, encoder, bank, workspace):
    writer = EventLogWriter(
        path=os.path.join(workspace, "lag.csv"),
        run_id=new_run_id(),
        source_id=live_session_id(),
        source_type=SOURCE_LIVE,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=0.8,
        source_start_utc=utc_now(),
    )
    detector = LiveDetector(cfg, encoder, bank, 0.8, writer)
    writer.close()
    # W_base/2 + smoothing * stride = 1.0 + 3 * 1.0
    assert detector.lag_sec == pytest.approx(4.0)


def test_live_and_offline_agree_on_a_clean_signal(cfg, encoder, bank, live_timeline, workspace):
    """Same features, same threshold -- the two paths must not disagree wildly.

    They are allowed to differ (live skips NMS and cross-class, spec 10.2), so
    this asserts overlap of the primary detection, not row-for-row equality.
    """
    frames, _ = live_timeline
    feats, starts = encode_timeline(encoder, frames, fps=FPS, chunk_sec=CHUNK_SEC)
    class_names, grid = score_grid(feats, bank, cfg)
    tau_high = float(np.percentile(grid, 70))

    offline = group_detections(class_names, grid, starts, cfg, tau_high, CHUNK_SEC)

    log_path = os.path.join(workspace, "agree.csv")
    writer = EventLogWriter(
        path=log_path,
        run_id=new_run_id(),
        source_id=live_session_id(),
        source_type=SOURCE_LIVE,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=tau_high,
        source_start_utc=utc_now(),
    )
    detector = LiveDetector(cfg, encoder, bank, tau_high, writer)
    for feature, start in zip(feats, starts):
        detector.push_chunk(feature, float(start))
    detector.flush(float(starts[-1]) + CHUNK_SEC)
    writer.close()

    live = [e for e in read_events(log_path) if e["status"] == "closed"]
    assert offline and live

    best = max(
        tiou((o["start"], o["end"]), (l["start_sec"], l["end_sec"]))
        for o in offline
        for l in live
    )
    assert best >= 0.5, "offline and live disagree on where the action is"

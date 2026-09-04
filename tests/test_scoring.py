"""Tests for window pooling, similarity, and grouping (spec 7)."""

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.encoder import fit_clip_length, l2_normalize, resample_frame_indices  # noqa: E402
from src.scoring import (  # noqa: E402
    HysteresisTracker,
    group_detections,
    pool_windows,
    resolve_cross_class,
    similarity_to_class,
    smooth,
    temporal_nms,
    tiou,
)


class FakeCfg(dict):
    """Minimal config stand-in for the grouping helpers."""

    def __init__(self, **kw):
        super().__init__(
            chunk_sec=1.0,
            w_base_sec=2.0,
            w_base_chunks=2,
            window_scales=[0.7, 1.0, 1.4],
            stride_ratio=0.25,
            topk_prototypes=3,
            smoothing_windows=3,
            tau_low_ratio=0.85,
            min_duration_ratio=0.5,
            nms_tiou=0.5,
            **kw,
        )

    def class_cfg(self, name):
        return self.get("classes", {}).get(name, {})

    @property
    def stride_chunks(self):
        return max(1, int(round(self["w_base_chunks"] * self["stride_ratio"])))

    def window_lengths_chunks(self):
        return sorted({max(1, int(round(self["w_base_chunks"] * s))) for s in self["window_scales"]})


# -- frame resampling (the shared encoding path) ---------------------------


def test_resample_upsamples_by_repeating():
    idx = resample_frame_indices(8, 64)
    assert len(idx) == 64
    assert idx[0] == 0 and idx[-1] == 7
    assert np.all(np.diff(idx) >= 0), "resampled indices must be monotonic"


def test_resample_downsamples_evenly():
    idx = resample_frame_indices(64, 8)
    assert len(idx) == 8
    assert idx[0] == 0 and idx[-1] == 63


def test_fit_clip_length_is_exact():
    clip = np.zeros((5, 8, 8, 3), dtype=np.uint8)
    assert fit_clip_length(clip, 16).shape == (16, 8, 8, 3)
    assert fit_clip_length(clip, 3).shape == (3, 8, 8, 3)


def test_fit_clip_rejects_wrong_shape():
    with pytest.raises(ValueError):
        fit_clip_length(np.zeros((5, 8, 8), dtype=np.uint8), 16)


# -- 7.1 pooling -----------------------------------------------------------


def test_pooled_windows_are_normalized():
    rng = np.random.default_rng(0)
    feats = l2_normalize(rng.normal(size=(20, 32)))
    windows, starts = pool_windows(feats, window_chunks=4, stride_chunks=2)
    norms = np.linalg.norm(windows, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-5), "re-normalization after pooling is mandatory"
    assert starts[0] == 0 and starts[1] == 2


def test_pooling_equals_explicit_mean():
    rng = np.random.default_rng(1)
    feats = l2_normalize(rng.normal(size=(10, 8)))
    windows, starts = pool_windows(feats, window_chunks=3, stride_chunks=1)
    expected = l2_normalize(feats[2:5].mean(axis=0)[None, :])[0]
    assert np.allclose(windows[list(starts).index(2)], expected, atol=1e-5)


def test_pooling_clamps_window_to_available_chunks():
    feats = l2_normalize(np.random.default_rng(2).normal(size=(3, 8)))
    windows, _ = pool_windows(feats, window_chunks=99, stride_chunks=1)
    assert len(windows) == 1


# -- 7.2 similarity --------------------------------------------------------


def test_topk_similarity_ignores_bad_variants():
    """Mean-of-top-3 must not be dragged down by off-distribution augmentations."""
    target = l2_normalize(np.array([[1.0, 0.0, 0.0]]))
    variants = l2_normalize(
        np.array(
            [
                [1.0, 0.02, 0.0],   # three good matches
                [1.0, 0.00, 0.03],
                [0.99, 0.05, 0.0],
                [0.0, 1.0, 0.0],    # two useless ones
                [0.0, 0.0, 1.0],
            ]
        )
    )
    top3 = similarity_to_class(target, variants, topk=3)[0]
    mean_all = float((target @ variants.T).mean())
    assert top3 > 0.98
    assert top3 > mean_all + 0.3


def test_topk_clamps_to_variant_count():
    target = l2_normalize(np.array([[1.0, 0.0]]))
    variants = l2_normalize(np.array([[1.0, 0.0], [0.0, 1.0]]))
    assert similarity_to_class(target, variants, topk=10).shape == (1,)


# -- 7.3.1 smoothing -------------------------------------------------------


def test_smooth_suppresses_single_frame_spike():
    series = np.array([0.1, 0.1, 0.9, 0.1, 0.1], dtype=np.float32)
    out = smooth(series, 3)
    assert out[2] < 0.5, "a lone spike must be damped"
    assert len(out) == len(series)


def test_smooth_is_identity_at_width_one():
    series = np.array([0.1, 0.9, 0.2], dtype=np.float32)
    assert np.allclose(smooth(series, 1), series)


# -- 7.3.2 hysteresis ------------------------------------------------------


def test_hysteresis_opens_high_and_closes_low():
    tracker = HysteresisTracker("a", tau_high=0.8, tau_low=0.68)
    opened, closed = tracker.step(0.0, 1.0, 0.9)
    assert opened and not closed and tracker.is_open

    # Between tau_low and tau_high the detection stays open.
    _, closed = tracker.step(1.0, 2.0, 0.72)
    assert closed is None and tracker.is_open

    _, closed = tracker.step(2.0, 3.0, 0.5)
    assert closed is not None and not tracker.is_open
    assert closed["start"] == 0.0 and closed["end"] == 3.0


def test_hysteresis_reports_peak_score():
    tracker = HysteresisTracker("a", 0.8, 0.68)
    tracker.step(0.0, 1.0, 0.85)
    tracker.step(1.0, 2.0, 0.95)
    tracker.step(2.0, 3.0, 0.70)
    _, closed = tracker.step(3.0, 4.0, 0.1)
    assert closed["score"] == pytest.approx(0.95)


def test_hysteresis_does_not_reopen_below_tau_high():
    tracker = HysteresisTracker("a", 0.8, 0.68)
    opened, _ = tracker.step(0.0, 1.0, 0.75)
    assert opened is None and not tracker.is_open


def test_class_tau_scale_raises_only_named_class():
    from src.scoring import class_tau

    class _Cfg(dict):
        def class_cfg(self, name):
            return self["classes"].get(name, {})

    cfg = _Cfg(tau_low_ratio=0.85, classes={
        "hair_twirling": {"tau_scale": 2.0},
        "head_nodding": {},
    })
    hair_hi, hair_lo = class_tau(cfg, "hair_twirling", 1.5)
    nod_hi, nod_lo = class_tau(cfg, "head_nodding", 1.5)
    assert hair_hi == pytest.approx(3.0)
    assert hair_lo == pytest.approx(3.0 * 0.85)
    assert nod_hi == pytest.approx(1.5)
    assert nod_lo == pytest.approx(1.5 * 0.85)


def test_force_close_bounds_a_stuck_detection():
    """spec 9.4 -- a pinned-high score must not hold one event open forever."""
    tracker = HysteresisTracker("a", 0.8, 0.68, max_open_sec=5.0)
    tracker.step(0.0, 1.0, 0.99)
    closed = None
    for t in range(1, 12):
        _, closed = tracker.step(float(t), float(t + 1), 0.99)
        if closed:
            break
    assert closed is not None and closed.get("forced") is True
    assert closed["end"] == pytest.approx(5.0)


def test_flush_closes_what_is_still_open():
    tracker = HysteresisTracker("a", 0.8, 0.68)
    tracker.step(0.0, 1.0, 0.9)
    closed = tracker.flush(4.0)
    assert closed["start"] == 0.0 and closed["end"] == 4.0
    assert tracker.flush(5.0) is None, "flushing twice must not invent an event"


def test_each_detection_gets_a_distinct_event_id():
    tracker = HysteresisTracker("a", 0.8, 0.68)
    tracker.step(0.0, 1.0, 0.9)
    _, first = tracker.step(1.0, 2.0, 0.1)
    tracker.step(2.0, 3.0, 0.9)
    _, second = tracker.step(3.0, 4.0, 0.1)
    assert first["event_id"] != second["event_id"]


# -- 7.3.3 - 7.3.5 ---------------------------------------------------------


def test_tiou_basics():
    assert tiou((0, 10), (0, 10)) == pytest.approx(1.0)
    assert tiou((0, 10), (20, 30)) == 0.0
    assert tiou((0, 10), (5, 15)) == pytest.approx(5 / 15)


def test_nms_merges_same_class_only():
    dets = [
        {"class": "a", "start": 0, "end": 10, "score": 0.9},
        {"class": "a", "start": 1, "end": 11, "score": 0.8},   # tIoU ~0.82 -> dropped
        {"class": "b", "start": 0, "end": 10, "score": 0.7},   # other class -> kept here
    ]
    kept = temporal_nms(dets, 0.5)
    assert len(kept) == 2
    assert {k["class"] for k in kept} == {"a", "b"}
    assert kept[0]["score"] == 0.9


def test_cross_class_keeps_highest_scoring():
    dets = [
        {"class": "a", "start": 0, "end": 10, "score": 0.9},
        {"class": "b", "start": 5, "end": 15, "score": 0.7},
    ]
    kept = resolve_cross_class(dets)
    assert len(kept) == 1 and kept[0]["class"] == "a"


def test_cross_class_leaves_disjoint_classes_alone():
    dets = [
        {"class": "a", "start": 0, "end": 10, "score": 0.9},
        {"class": "b", "start": 20, "end": 30, "score": 0.7},
    ]
    assert len(resolve_cross_class(dets)) == 2


# -- full grouping ---------------------------------------------------------


def test_group_detections_recovers_a_contiguous_high_region():
    cfg = FakeCfg()
    starts = np.arange(20, dtype=np.float32)
    grid = np.full((1, 20), 0.2, dtype=np.float32)
    grid[0, 5:12] = 0.95
    detections = group_detections(["a"], grid, starts, cfg, tau_high=0.8)

    assert len(detections) == 1
    det = detections[0]
    assert tiou((det["start"], det["end"]), (5.0, 12.0)) >= 0.5


def test_group_detections_drops_too_short():
    """min_duration_ratio 0.5 x W_base 2.0s = 1.0s floor."""
    cfg = FakeCfg()
    starts = np.arange(20, dtype=np.float32)
    grid = np.full((1, 20), 0.2, dtype=np.float32)
    grid[0, 7] = 0.95          # a single chunk, smoothed to well under 1s
    assert group_detections(["a"], grid, starts, cfg, tau_high=0.8) == []


def test_group_detections_finds_nothing_in_flat_background():
    cfg = FakeCfg()
    starts = np.arange(30, dtype=np.float32)
    grid = np.full((1, 30), 0.3, dtype=np.float32)
    assert group_detections(["a"], grid, starts, cfg, tau_high=0.8) == []


def test_group_detections_separates_two_instances():
    cfg = FakeCfg()
    starts = np.arange(40, dtype=np.float32)
    grid = np.full((1, 40), 0.1, dtype=np.float32)
    grid[0, 4:10] = 0.95
    grid[0, 25:32] = 0.95
    detections = group_detections(["a"], grid, starts, cfg, tau_high=0.8)
    assert len(detections) == 2
    assert detections[0]["end"] < detections[1]["start"]

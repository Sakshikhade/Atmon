"""Tests for the pose stream's math (spec 11.3).

Pure numpy: normalization, DTW, similarity, and score-level fusion. MediaPipe
extraction itself is not covered -- it needs the model file and real frames.
"""


import numpy as np
import pytest

from src.pose import (  # noqa: E402
    L_SHOULDER,
    R_SHOULDER,
    UPPER_BODY,
    dtw_distance,
    normalize_pose,
    similarity,
    wrist_near_ear,
)
from src.scoring import aggregate_per_chunk, fuse_scores  # noqa: E402

K = len(UPPER_BODY)

def skeleton(hand_offset, center=(0.5, 0.5), scale=0.2):
    """One frame: shoulders at +/- scale, a 'hand' landmark at an offset.

    Unset landmarks are NaN, not zero. A zero here would be a real point at the
    image origin that moves relative to the torso as the subject moves, which
    would break translation invariance for reasons the fixture invented.
    """
    frame = np.full((K, 3), np.nan, dtype=np.float32)
    cx, cy = center
    frame[L_SHOULDER] = (cx - scale, cy, 0.0)
    frame[R_SHOULDER] = (cx + scale, cy, 0.0)
    frame[0] = (cx, cy - scale, 0.0)                     # nose/head
    frame[15] = (cx + hand_offset[0] * scale, cy + hand_offset[1] * scale, 0.0)
    return frame

def sequence(offsets, center=(0.5, 0.5), scale=0.2):
    return np.stack([skeleton(o, center, scale) for o in offsets])

def test_normalization_is_translation_invariant():
    a = normalize_pose(sequence([(0.5, -1.0)], center=(0.3, 0.3)))
    b = normalize_pose(sequence([(0.5, -1.0)], center=(0.8, 0.7)))
    assert np.allclose(a, b, atol=1e-5, equal_nan=True), "same gesture, different position"

def test_normalization_is_scale_invariant():
    """A close-up and a medium shot of the same gesture must normalize alike.

    This is precisely the domain gap that hurts the appearance stream when the
    reference clips are framed differently from the target footage.
    """
    near = normalize_pose(sequence([(0.5, -1.0)], scale=0.35))
    far = normalize_pose(sequence([(0.5, -1.0)], scale=0.12))
    assert np.allclose(near, far, atol=1e-5, equal_nan=True)

def test_normalization_skips_frames_without_shoulders():
    seq = sequence([(0.5, -1.0), (0.5, -1.0)])
    seq[1, L_SHOULDER] = np.nan
    out = normalize_pose(seq)
    assert not np.isnan(out[0]).all()
    assert np.isnan(out[1]).all(), "a frame with no shoulders cannot be normalized"

def test_dtw_is_zero_for_identical_sequences():
    seq = normalize_pose(sequence([(0.2, -0.5), (0.5, -1.0), (0.2, -0.5)]))
    assert dtw_distance(seq, seq) == pytest.approx(0.0, abs=1e-6)

def test_dtw_separates_different_gestures():
    hand_up = normalize_pose(sequence([(0.5, -1.0)] * 4))       # hand by the head
    hand_down = normalize_pose(sequence([(0.5, 1.5)] * 4))      # hand by the hip
    assert dtw_distance(hand_up, hand_up) < dtw_distance(hand_up, hand_down)

def test_dtw_tolerates_timing_differences():
    """The same gesture performed slower must still match closely."""
    fast = normalize_pose(sequence([(0.2, -0.4), (0.5, -1.0), (0.2, -0.4)]))
    slow = normalize_pose(sequence([(0.2, -0.4)] * 2 + [(0.5, -1.0)] * 2 + [(0.2, -0.4)] * 2))
    different = normalize_pose(sequence([(0.5, 1.5)] * 6))
    assert dtw_distance(fast, slow) < dtw_distance(fast, different)

def test_dtw_returns_inf_without_a_usable_pose():
    good = normalize_pose(sequence([(0.5, -1.0)]))
    empty = np.full((3, K, 3), np.nan, dtype=np.float32)
    assert not np.isfinite(dtw_distance(good, empty))

def test_similarity_is_bounded_and_ordered():
    template = normalize_pose(sequence([(0.5, -1.0)] * 4))
    same = normalize_pose(sequence([(0.5, -1.0)] * 4))
    other = normalize_pose(sequence([(0.5, 1.5)] * 4))
    s_same = similarity(same, [template])
    s_other = similarity(other, [template])
    assert 0.0 < s_same <= 1.0 and 0.0 <= s_other <= 1.0
    assert s_same > s_other

def test_similarity_takes_the_best_template():
    a = normalize_pose(sequence([(0.5, -1.0)] * 3))
    b = normalize_pose(sequence([(-0.5, -1.0)] * 3))
    query = normalize_pose(sequence([(-0.5, -1.0)] * 3))
    assert similarity(query, [a, b]) == pytest.approx(similarity(query, [b]))

def test_similarity_of_no_pose_is_zero():
    template = normalize_pose(sequence([(0.5, -1.0)]))
    assert similarity(np.full((3, K, 3), np.nan, dtype=np.float32), [template]) == 0.0

# -- window aggregation and fusion ----------------------------------------

class FakeCfg(dict):
    def __init__(self, alphas=None, **kw):
        super().__init__(w_base_chunks=2, window_scales=[0.7, 1.0, 1.4],
                         stride_ratio=0.25, normalize_scores=True, **kw)
        self._alphas = alphas or {}

    @property
    def stride_chunks(self):
        return max(1, int(round(self["w_base_chunks"] * self["stride_ratio"])))

    def window_lengths_chunks(self):
        return sorted({max(1, int(round(self["w_base_chunks"] * s))) for s in self["window_scales"]})

    def class_cfg(self, name):
        return self._alphas.get(name, {})

def test_aggregate_spreads_a_peak_over_its_window():
    cfg = FakeCfg()
    series = np.zeros(12, dtype=np.float32)
    series[6] = 1.0
    out = aggregate_per_chunk(series, cfg)
    assert out[6] > 0, "the peak survives"
    assert out[4] > 0 or out[8] > 0, "windows covering it lift neighbours too"
    assert out[0] == 0.0, "far-away chunks are untouched"

def test_aggregate_preserves_length():
    cfg = FakeCfg()
    assert len(aggregate_per_chunk(np.zeros(7, np.float32), cfg)) == 7

def test_explicit_weights_override_alpha():
    """Three-stream weights are normalized and honoured per class."""
    from src.scoring import stream_weights

    cfg = FakeCfg(alphas={"a": {"weights": {"vjepa": 1.0, "pose": 2.0, "hands": 1.0}}})
    w = stream_weights(cfg, "a", ["vjepa", "pose", "hands"])
    assert w["pose"] == pytest.approx(0.5)
    assert sum(w.values()) == pytest.approx(1.0)

def test_alpha_splits_remaining_weight_across_other_streams():
    """alpha_vjepa keeps meaning its two-way split when a third stream exists."""
    from src.scoring import stream_weights

    cfg = FakeCfg(alphas={"a": {"alpha_vjepa": 0.4}})
    w = stream_weights(cfg, "a", ["vjepa", "pose", "hands"])
    assert w["vjepa"] == pytest.approx(0.4)
    assert w["pose"] == pytest.approx(0.3) and w["hands"] == pytest.approx(0.3)

def test_fusion_follows_the_hand_stream_when_weighted_to_it():
    cfg = FakeCfg(alphas={"a": {"weights": {"vjepa": 0.0, "pose": 0.0, "hands": 1.0}}})
    rng = np.random.default_rng(7)
    grids = {k: rng.normal(0.5, 0.01, (1, 60)).astype(np.float32)
             for k in ("vjepa", "pose", "hands")}
    grids["hands"][0, 20:25] += 0.3
    fused = fuse_scores(["a"], grids, cfg)
    assert fused[0, 22] > 3.0

def test_fusion_respects_alpha():
    cfg = FakeCfg(alphas={"a": {"alpha_vjepa": 0.0}, "b": {"alpha_vjepa": 1.0}})
    rng = np.random.default_rng(0)
    # Class a: signal only in pose. Class b: signal only in vjepa.
    vjepa = np.stack([rng.normal(0.9, 0.002, 60), rng.normal(0.9, 0.002, 60)]).astype(np.float32)
    pose = np.stack([rng.normal(0.2, 0.01, 60), rng.normal(0.2, 0.01, 60)]).astype(np.float32)
    pose[0, 30:35] += 0.5
    vjepa[1, 30:35] += 0.05

    fused = fuse_scores(["a", "b"], {"vjepa": vjepa, "pose": pose}, cfg)
    assert fused[0, 32] > 3.0, "alpha 0.0 must follow the pose stream"
    assert fused[1, 32] > 3.0, "alpha 1.0 must follow the embedding stream"

def test_fusion_without_pose_still_normalizes():
    cfg = FakeCfg()
    rng = np.random.default_rng(3)
    vjepa = rng.normal(0.9, 0.004, (1, 100)).astype(np.float32)
    out = fuse_scores(["a"], {"vjepa": vjepa}, cfg)
    assert abs(float(np.median(out[0]))) < 0.5, "should be centred on background"

def test_fusion_half_and_half_uses_both():
    cfg = FakeCfg(alphas={"a": {"alpha_vjepa": 0.5}})
    rng = np.random.default_rng(4)
    vjepa = rng.normal(0.9, 0.002, (1, 60)).astype(np.float32)
    pose = rng.normal(0.2, 0.01, (1, 60)).astype(np.float32)
    vjepa[0, 30:33] += 0.02
    pose[0, 30:33] += 0.4
    fused = fuse_scores(["a"], {"vjepa": vjepa, "pose": pose}, cfg)
    only_pose = fuse_scores(["a"], {"vjepa": vjepa * 0 + 0.9, "pose": pose},
                            FakeCfg(alphas={"a": {"alpha_vjepa": 0.0}}))
    assert fused[0, 31] > 0
    assert fused[0, 31] < only_pose[0, 31] + 1e-3

def test_hysteresis_can_be_blocked_from_opening():
    """allow_open=False is how live does cross-class resolution online."""
    from src.scoring import HysteresisTracker

    tracker = HysteresisTracker("a", tau_high=0.8, tau_low=0.68)
    opened, _ = tracker.step(0.0, 1.0, 0.95, allow_open=False)
    assert opened is None and not tracker.is_open, "a blocked class must not open"

    opened, _ = tracker.step(1.0, 2.0, 0.95, allow_open=True)
    assert opened is not None and tracker.is_open, "and must still open once allowed"

def test_wrist_near_ear_detects_hand_at_head():
    # Already shoulder-normalized: shoulders at +/-0.5, left wrist by left ear.
    seq = np.full((4, K, 3), np.nan, dtype=np.float32)
    for t in range(4):
        seq[t, L_SHOULDER] = (-0.5, 0.0, 0.0)
        seq[t, R_SHOULDER] = (0.5, 0.0, 0.0)
        seq[t, 7] = (-0.4, -0.6, 0.0)   # L_EAR
        seq[t, 8] = (0.4, -0.6, 0.0)
        seq[t, 15] = (-0.35, -0.55, 0.0)  # L_WRIST near ear
        seq[t, 16] = (0.5, 0.8, 0.0)
    assert wrist_near_ear(seq, threshold=0.28) is True

    seq[:, 15] = (0.5, 0.8, 0.0)
    assert wrist_near_ear(seq, threshold=0.28) is False

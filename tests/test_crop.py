"""Tests for pose-derived cropping and hand normalization."""


import numpy as np
import pytest

from src.crop import (  # noqa: E402
    FULL_FRAME,
    apply_box,
    box_from_landmarks,
    crop_tag,
    smooth_boxes,
)
from src.hands import MIDDLE_MCP, N_HAND_LANDMARKS, WRIST, normalize_hand  # noqa: E402

K = 25

def landmarks(points, n_frames=1):
    """[T, K, 3] with the given {index: (x, y)} filled and the rest NaN."""
    out = np.full((n_frames, K, 3), np.nan, dtype=np.float32)
    for idx, (x, y) in points.items():
        out[:, idx] = (x, y, 0.0)
    return out

def test_no_pose_gives_the_full_frame():
    assert box_from_landmarks(np.full((3, K, 3), np.nan, np.float32)) == FULL_FRAME

def test_box_contains_the_landmarks():
    # nose, shoulders, wrists clustered mid-frame
    lm = landmarks({0: (0.50, 0.30), 11: (0.42, 0.45), 12: (0.58, 0.45),
                    15: (0.40, 0.35), 16: (0.60, 0.35)})
    x0, y0, x1, y1 = box_from_landmarks(lm, padding=0.2)
    assert x0 <= 0.40 and x1 >= 0.60
    assert y0 <= 0.30 and y1 >= 0.45

def test_box_stays_inside_the_frame():
    lm = landmarks({0: (0.02, 0.02), 11: (0.05, 0.10), 12: (0.12, 0.10)})
    x0, y0, x1, y1 = box_from_landmarks(lm, padding=0.5)
    assert 0.0 <= x0 < x1 <= 1.0
    assert 0.0 <= y0 < y1 <= 1.0

def test_min_size_floors_a_tiny_subject():
    """A distant subject must not produce a postage-stamp crop."""
    lm = landmarks({0: (0.50, 0.50), 11: (0.49, 0.51), 12: (0.51, 0.51)})
    x0, y0, x1, y1 = box_from_landmarks(lm, padding=0.1, min_size=0.4)
    assert (x1 - x0) >= 0.4 - 1e-6

def test_padding_grows_the_box():
    lm = landmarks({0: (0.4, 0.4), 11: (0.35, 0.5), 12: (0.55, 0.5), 15: (0.3, 0.45)})
    tight = box_from_landmarks(lm, padding=0.0, min_size=0.0)
    loose = box_from_landmarks(lm, padding=0.6, min_size=0.0)
    assert (loose[2] - loose[0]) > (tight[2] - tight[0])

def test_box_spans_the_whole_clip_not_one_frame():
    """A moving hand must stay inside the box for the clip's duration.

    A per-frame box would track the hand and cancel the motion being encoded.
    """
    lm = np.full((3, K, 3), np.nan, dtype=np.float32)
    for t, hand_x in enumerate((0.30, 0.50, 0.70)):
        lm[t, 0] = (0.5, 0.3, 0.0)
        lm[t, 11] = (0.45, 0.45, 0.0)
        lm[t, 12] = (0.55, 0.45, 0.0)
        lm[t, 15] = (hand_x, 0.35, 0.0)
    x0, _, x1, _ = box_from_landmarks(lm, padding=0.1, min_size=0.0)
    assert x0 <= 0.30 and x1 >= 0.70

def test_smoothing_suppresses_a_single_bad_box():
    """One bad chunk must not drag its neighbours -- hence median, not mean."""
    boxes = np.array([[0.2, 0.2, 0.8, 0.8]] * 5, dtype=np.float32)
    boxes[2] = [0.0, 0.0, 1.0, 1.0]
    out = smooth_boxes(boxes, window=5)
    assert np.allclose(out[2], [0.2, 0.2, 0.8, 0.8], atol=1e-6)

def test_smoothing_preserves_shape_and_short_inputs():
    boxes = np.array([[0.1, 0.1, 0.9, 0.9], [0.2, 0.2, 0.8, 0.8]], dtype=np.float32)
    assert smooth_boxes(boxes, 5).shape == boxes.shape

def test_apply_box_crops_the_expected_region():
    frames = np.zeros((2, 100, 200, 3), dtype=np.uint8)
    frames[:, 40:60, 80:120] = 255
    out = apply_box(frames, (0.4, 0.4, 0.6, 0.6))
    assert out.shape[1:3] == (20, 40)
    assert out.min() == 255, "the crop should contain only the bright region"

def test_apply_box_none_is_a_no_op():
    frames = np.zeros((1, 10, 10, 3), dtype=np.uint8)
    assert apply_box(frames, None).shape == frames.shape

def test_apply_box_never_produces_an_empty_crop():
    frames = np.zeros((1, 50, 50, 3), dtype=np.uint8)
    out = apply_box(frames, (0.5, 0.5, 0.5, 0.5))
    assert out.shape[1] >= 1 and out.shape[2] >= 1

class FakeCfg(dict):
    def __init__(self, **kw):
        super().__init__(**kw)

def test_crop_tag_keys_the_cache():
    """Changing crop settings must invalidate features (pitfall 14.6)."""
    off = FakeCfg(crop={"enabled": False})
    a = FakeCfg(crop={"enabled": True, "padding": 0.35, "min_size": 0.25, "smooth_window": 5})
    b = FakeCfg(crop={"enabled": True, "padding": 0.50, "min_size": 0.25, "smooth_window": 5})
    assert crop_tag(off) == "none"
    assert crop_tag(a) != crop_tag(off)
    assert crop_tag(a) != crop_tag(b), "different padding must key differently"

# -- hand normalization ----------------------------------------------------

def hand(points, span=0.1, offset=(0.5, 0.5)):
    out = np.full((1, N_HAND_LANDMARKS, 3), np.nan, dtype=np.float32)
    ox, oy = offset
    out[0, WRIST] = (ox, oy, 0.0)
    out[0, MIDDLE_MCP] = (ox, oy - span, 0.0)
    for idx, (dx, dy) in points.items():
        out[0, idx] = (ox + dx * span, oy + dy * span, 0.0)
    return out

def test_hand_normalization_puts_the_wrist_at_the_origin():
    out = normalize_hand(hand({4: (1.0, -1.0)}))
    assert np.allclose(out[0, WRIST], [0, 0, 0], atol=1e-5)

def test_hand_normalization_is_translation_and_scale_invariant():
    """Same finger configuration, different place and apparent size."""
    near = normalize_hand(hand({4: (1.0, -1.0), 8: (0.2, -2.0)}, span=0.20, offset=(0.3, 0.3)))
    far = normalize_hand(hand({4: (1.0, -1.0), 8: (0.2, -2.0)}, span=0.05, offset=(0.8, 0.7)))
    assert np.allclose(near, far, atol=1e-4, equal_nan=True)

def test_hand_normalization_separates_configurations():
    a = normalize_hand(hand({8: (0.0, -2.0)}))     # index extended
    b = normalize_hand(hand({8: (0.0, 0.5)}))      # index curled
    assert not np.allclose(a[0, 8], b[0, 8], atol=1e-3)

def test_hand_normalization_skips_frames_without_a_wrist():
    seq = hand({4: (1.0, -1.0)}, offset=(0.5, 0.5))
    seq = np.concatenate([seq, np.full_like(seq, np.nan)])
    out = normalize_hand(seq)
    assert not np.isnan(out[0]).all()
    assert np.isnan(out[1]).all()

def test_hand_normalization_ignores_a_degenerate_span():
    seq = hand({}, span=0.0)
    assert np.isnan(normalize_hand(seq)[0]).all()

def test_out_of_frame_landmarks_are_clipped():
    """MediaPipe extrapolates occluded joints past the frame edge.

    Measured on real footage: a seated subject produced a hip landmark at
    y = 1.94, inflating the box to 137% of the frame and turning every crop
    into a silent no-op.
    """
    lm = landmarks({0: (0.5, 0.30), 11: (0.40, 0.50), 12: (0.60, 0.50),
                    23: (0.45, 1.94), 24: (0.55, 1.90)})
    x0, y0, x1, y1 = box_from_landmarks(lm, padding=0.1, min_size=0.0)
    assert y1 <= 1.0 and y0 >= 0.0
    assert (y1 - y0) < 1.0, "an out-of-frame joint must not force a full-frame box"

def test_box_preserves_the_source_aspect_ratio():
    """Square in normalized coords == source pixel aspect, so no stretching."""
    lm = landmarks({0: (0.5, 0.3), 11: (0.45, 0.4), 12: (0.55, 0.4), 15: (0.4, 0.35)})
    x0, y0, x1, y1 = box_from_landmarks(lm, padding=0.2, min_size=0.1)
    assert abs((x1 - x0) - (y1 - y0)) < 1e-5

def test_a_small_distant_subject_actually_gets_cropped():
    """The case cropping exists for: subject occupying a corner of a room shot."""
    lm = landmarks({0: (0.20, 0.20), 11: (0.17, 0.26), 12: (0.23, 0.26),
                    15: (0.15, 0.23), 16: (0.25, 0.23)})
    x0, y0, x1, y1 = box_from_landmarks(lm, padding=0.35, min_size=0.15)
    assert (x1 - x0) < 0.6, "should zoom in substantially"
    assert x0 <= 0.15 and x1 >= 0.25, "and still contain the subject"

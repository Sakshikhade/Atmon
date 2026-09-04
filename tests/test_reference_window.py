"""Highest-motion reference window selection."""

import numpy as np

from src.prototypes import select_reference_window


def test_select_reference_window_picks_motion_peak():
    # Still frames, then a burst of motion, then still again.
    clip = np.zeros((40, 8, 8, 3), dtype=np.uint8)
    clip[20:28] = 255  # motion between 19-20 and across the bright block
    kept, start = select_reference_window(clip, n_keep=8)
    assert kept.shape[0] == 8
    # Window should overlap the bright burst, not the leading stillness.
    assert 12 <= start <= 24


def test_select_reference_window_short_clip_unchanged():
    clip = np.zeros((5, 4, 4, 3), dtype=np.uint8)
    kept, start = select_reference_window(clip, n_keep=8)
    assert start == 0
    assert len(kept) == 5

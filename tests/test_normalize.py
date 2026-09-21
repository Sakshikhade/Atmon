"""Tests for background-relative score normalization."""


import numpy as np
import pytest

from src.normalize import (  # noqa: E402
    RunningBackground,
    robust_center_scale,
    zscore_grid,
    zscore_series,
)

def test_robust_stats_ignore_an_action_minority():
    """Median/MAD must not be dragged up by the events being standardized."""
    background = np.full(90, 0.89)
    background[::3] += 0.002                      # a little spread
    action = np.full(10, 0.99)                    # 10% of samples, far above
    center, scale = robust_center_scale(np.concatenate([background, action]))
    assert center == pytest.approx(0.89, abs=0.005)
    assert scale > 0

def test_zscore_puts_background_at_zero_and_action_high():
    rng = np.random.default_rng(0)
    series = np.concatenate([rng.normal(0.89, 0.004, 90), rng.normal(0.93, 0.004, 10)])
    z = zscore_series(series)
    assert abs(float(np.median(z[:90]))) < 0.5, "background should sit near zero sigma"
    assert float(np.median(z[90:])) > 3.0, "action should sit several sigma up"

def test_zscore_is_scene_invariant():
    """The same pattern shifted to a different absolute band standardizes alike.

    This is the whole point: raw cosines move when the room changes, so an
    absolute threshold cannot transfer. Sigma-above-background can.
    """
    pattern = np.array([0.0, 0.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 0.0, 0.0])
    room_a = 0.89 + 0.01 * pattern
    room_b = 0.74 + 0.01 * pattern            # different scene, same structure
    assert np.allclose(zscore_series(room_a), zscore_series(room_b), atol=1e-4)

def test_constant_series_does_not_divide_by_zero():
    z = zscore_series(np.full(50, 0.9))
    assert np.all(np.isfinite(z))

def test_zscore_grid_normalizes_each_class_independently():
    """A class sitting closer to this scene must not dominate the others."""
    rng = np.random.default_rng(1)
    grid = np.stack([rng.normal(0.90, 0.005, 100), rng.normal(0.60, 0.005, 100)])
    z = zscore_grid(grid)
    assert abs(float(np.median(z[0]))) < 0.5
    assert abs(float(np.median(z[1]))) < 0.5, "the lower-baseline class recenters too"

def test_empty_input_is_safe():
    assert zscore_series(np.array([])).size == 0
    assert robust_center_scale(np.array([])) == (0.0, 1.0)

# -- streaming -------------------------------------------------------------

def test_running_background_needs_warmup():
    rng = np.random.default_rng(11)
    running = RunningBackground(window=100, warmup=30)
    values = rng.normal(0.89, 0.004, 30)
    for v in values[:29]:
        running.update(float(v))
    assert not running.ready, "must not be trusted before warmup"
    running.update(float(values[29]))
    assert running.ready

def test_running_background_rejects_a_degenerate_estimate():
    """Sample count is not enough -- the samples must actually vary.

    Identical scores give a MAD of zero, the scale drops to its floor, and the
    next ordinary fluctuation reads as hundreds of sigma. Live, that fired a
    detection the moment warmup ended.
    """
    flat = RunningBackground(window=100, warmup=5)
    for _ in range(50):
        flat.update(0.89)                          # no spread at all
    assert not flat.ready, "a spreadless estimate is not usable"

    varied = RunningBackground(window=100, warmup=5)
    for i in range(50):
        varied.update(0.89 + (i % 3) * 0.004)
    assert varied.ready, "once the scores actually vary, the estimate is usable"

def test_running_background_tracks_the_bulk():
    rng = np.random.default_rng(2)
    running = RunningBackground(window=200, warmup=10)
    for v in rng.normal(0.89, 0.004, 150):
        running.update(float(v))
    center, scale = running.stats
    assert center == pytest.approx(0.89, abs=0.003)
    assert running.normalize(0.89) == pytest.approx(0.0, abs=0.6)
    assert running.normalize(0.93) > 3.0

def test_running_background_adapts_to_a_scene_change():
    """A live session that moves to another room must recenter."""
    running = RunningBackground(window=50, warmup=10)
    for _ in range(50):
        running.update(0.89)
    assert running.normalize(0.89) == pytest.approx(0.0, abs=0.5)

    for _ in range(50):                        # walked into a different room
        running.update(0.72)
    assert running.stats[0] == pytest.approx(0.72, abs=0.01)
    assert running.normalize(0.72) == pytest.approx(0.0, abs=0.5)

def test_running_window_is_bounded():
    running = RunningBackground(window=25, warmup=5)
    for i in range(500):
        running.update(0.9 + i * 1e-6)
    assert len(running.history) == 25

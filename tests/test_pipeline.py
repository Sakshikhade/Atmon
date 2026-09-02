"""Unit tests for the shared detection/event-transition core (src/pipeline.py)."""

from unittest.mock import MagicMock

import pytest

from src.pipeline import EventTracker, compute_normalized_distance


def _landmarks(l_wrist, r_wrist, l_shoulder=(0.4, 0.5, 0.0), r_shoulder=(0.6, 0.5, 0.0)):
    """Build a 33-point mock landmark list with the 4 relevant joints set."""
    lm = [MagicMock() for _ in range(33)]

    def _set(i, xyz):
        lm[i].x, lm[i].y, lm[i].z = xyz

    _set(11, l_shoulder)
    _set(12, r_shoulder)
    _set(15, l_wrist)
    _set(16, r_wrist)
    return lm


# ---------------------------------------------------------------------------
# compute_normalized_distance
# ---------------------------------------------------------------------------
def test_distance_is_scale_invariant():
    # Same pose, two scales — normalized distance must match.
    close = _landmarks(
        l_wrist=(0.4, 0.7, 0.0),
        r_wrist=(0.6, 0.5, 0.0),
        l_shoulder=(0.4, 0.5, 0.0),
        r_shoulder=(0.6, 0.5, 0.0),
    )
    far = _landmarks(
        l_wrist=(0.45, 0.6, 0.0),
        r_wrist=(0.55, 0.5, 0.0),
        l_shoulder=(0.45, 0.5, 0.0),
        r_shoulder=(0.55, 0.5, 0.0),
    )
    assert compute_normalized_distance(close) == pytest.approx(compute_normalized_distance(far))


def test_distance_takes_max_of_both_arms():
    # Left wrist far from left shoulder, right wrist at rest — expect the left value.
    lm = _landmarks(l_wrist=(0.4, 0.9, 0.0), r_wrist=(0.6, 0.5, 0.0))
    # shoulder width = 0.2; left wrist-shoulder = 0.4 -> norm 2.0; right = 0 -> norm 0.
    assert compute_normalized_distance(lm) == pytest.approx(2.0)


def test_distance_degenerate_shoulders_returns_zero():
    lm = _landmarks(
        l_wrist=(0.4, 0.9, 0.0),
        r_wrist=(0.6, 0.9, 0.0),
        l_shoulder=(0.5, 0.5, 0.0),
        r_shoulder=(0.5, 0.5, 0.0),  # coincident
    )
    assert compute_normalized_distance(lm) == 0.0


def test_distance_missing_landmarks_returns_zero():
    assert compute_normalized_distance(None) == 0.0
    assert compute_normalized_distance([MagicMock() for _ in range(5)]) == 0.0


# ---------------------------------------------------------------------------
# EventTracker
# ---------------------------------------------------------------------------
def test_start_then_resolved_emits_two_edges_with_duration():
    tracker = EventTracker()

    # Rising edge at t=100
    start_events = tracker.update(stimming=True, ears_covered=False, eyes_closed=False, now=100.0)
    assert len(start_events) == 1
    assert start_events[0].behavior == "stimming"
    assert start_events[0].phase == "start"

    # Held — no new edge
    assert tracker.update(stimming=True, ears_covered=False, eyes_closed=False, now=105.0) == []

    # Falling edge at t=108 -> resolved with duration 8.0
    resolved = tracker.update(stimming=False, ears_covered=False, eyes_closed=False, now=108.0)
    assert len(resolved) == 1
    ev = resolved[0]
    assert ev.phase == "resolved"
    assert ev.event_type == "stimming"
    assert ev.subtype == "rhythmic_stimming"
    assert ev.action == "repetitive_wrist_motion"
    assert ev.started_at == 100.0
    assert ev.ended_at == 108.0
    assert ev.duration_s == 8.0


def test_no_double_fire_while_held_or_while_off():
    tracker = EventTracker()
    tracker.update(stimming=True, ears_covered=False, eyes_closed=False, now=0.0)
    # Repeated 'on' frames produce no further events.
    for t in (1.0, 2.0, 3.0):
        assert tracker.update(stimming=True, ears_covered=False, eyes_closed=False, now=t) == []
    tracker.update(stimming=False, ears_covered=False, eyes_closed=False, now=4.0)
    # Repeated 'off' frames produce no further events.
    for t in (5.0, 6.0):
        assert tracker.update(stimming=False, ears_covered=False, eyes_closed=False, now=t) == []


def test_three_behaviors_are_independent():
    tracker = EventTracker()
    events = tracker.update(stimming=True, ears_covered=True, eyes_closed=False, now=10.0)
    behaviors = {e.behavior for e in events}
    assert behaviors == {"stimming", "ears_covered"}
    assert all(e.phase == "start" for e in events)
    assert tracker.active("stimming") and tracker.active("ears_covered")
    assert not tracker.active("eyes_closed")

    # Resolve only ears_covered
    events = tracker.update(stimming=True, ears_covered=False, eyes_closed=False, now=15.0)
    assert len(events) == 1
    assert events[0].behavior == "ears_covered"
    assert events[0].phase == "resolved"
    assert events[0].duration_s == 5.0
    # stimming still active
    assert tracker.active("stimming")


def test_avoidance_labels():
    tracker = EventTracker()
    tracker.update(stimming=False, ears_covered=False, eyes_closed=True, now=0.0)
    ev = tracker.update(stimming=False, ears_covered=False, eyes_closed=False, now=2.0)[0]
    assert ev.event_type == "avoidance"
    assert ev.subtype == "eyes_closed_avoidance"
    assert ev.action == "eyes_closed"
    assert ev.duration_s == 2.0

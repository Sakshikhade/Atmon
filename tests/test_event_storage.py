"""Tests for EventStorage and skeleton snapshot generation."""

from collections import deque
from unittest.mock import MagicMock

from src.event_storage import EventStorage, build_snapshot


def _dummy_pose(x: float = 0.5, y: float = 0.5):
    """Generate 33 mock landmarks with specified coordinates."""
    pts = []
    for _ in range(33):
        m = MagicMock()
        m.x = x
        m.y = y
        m.z = 0.0
        pts.append(m)
    return pts


def test_build_snapshot_valid():
    buffer = deque(maxlen=60)
    for i in range(20):
        t = 100.0 + (i * 0.05)  # 20 fps over 1 second
        buffer.append((t, _dummy_pose(0.4, 0.6)))

    snapshot = build_snapshot(
        landmark_buffer=buffer,
        started_at=100.0,
        ended_at=101.0,
        snapshot_fps=10.0,
        max_duration_s=2.0,
    )

    assert snapshot is not None
    assert snapshot["fps"] == 10.0
    assert len(snapshot["frames"]) > 0
    first_frame = snapshot["frames"][0]
    assert "t" in first_frame
    assert "points" in first_frame
    # Check that points include standard joints
    point_indices = [p["i"] for p in first_frame["points"]]
    assert 0 in point_indices  # nose
    assert 11 in point_indices  # left shoulder
    assert 15 in point_indices  # left wrist


def test_build_snapshot_empty_or_none():
    assert build_snapshot([], 100.0, 102.0) is None
    assert build_snapshot(None, 100.0, 102.0) is None
    assert build_snapshot([(100.0, _dummy_pose())], None, 102.0) is None


def test_event_storage_crud(tmp_path):
    db_file = str(tmp_path / "outbox.db")
    storage = EventStorage(db_path=db_file)

    # 1. Add event without snapshot
    ev1_id = storage.add_event(
        type="stimming",
        subtype="rhythmic_stimming",
        action="repetitive_wrist_motion",
        started_at="2026-09-02T10:00:00Z",
        ended_at="2026-09-02T10:00:03Z",
        duration_s=3.0,
        intensity=0.08,
        source="live",
    )
    assert ev1_id > 0

    # 2. Add event with snapshot
    snapshot_payload = {
        "fps": 10.0,
        "frames": [
            {"t": 0.0, "points": [{"i": 0, "x": 0.5, "y": 0.5, "z": 0.0}]},
            {"t": 0.1, "points": [{"i": 0, "x": 0.51, "y": 0.52, "z": 0.0}]},
        ],
    }
    ev2_id = storage.add_event(
        type="avoidance",
        subtype="ears_covered_avoidance",
        action="hand_to_ear",
        started_at="2026-09-02T10:05:00Z",
        ended_at="2026-09-02T10:05:02Z",
        duration_s=2.0,
        source="ingest",
        snapshot=snapshot_payload,
    )
    assert ev2_id > ev1_id

    # 3. Query episodes
    episodes = storage.get_episodes()
    assert len(episodes) == 2
    assert episodes[0]["id"] == ev2_id  # Latest first
    assert episodes[0]["has_snapshot"] == 1
    assert episodes[1]["id"] == ev1_id
    assert episodes[1]["has_snapshot"] == 0

    # 4. Query with filters
    stimming_eps = storage.get_episodes(behavior_type="stimming")
    assert len(stimming_eps) == 1
    assert stimming_eps[0]["id"] == ev1_id

    live_eps = storage.get_episodes(source="live")
    assert len(live_eps) == 1
    assert live_eps[0]["id"] == ev1_id

    # 5. Fetch single episode with parsed snapshot
    ep2_data = storage.get_episode(ev2_id)
    assert ep2_data is not None
    assert ep2_data["snapshot"] is not None
    assert len(ep2_data["snapshot"]["frames"]) == 2

    ep1_data = storage.get_episode(ev1_id)
    assert ep1_data is not None
    assert ep1_data["snapshot"] is None

    # 6. Fetch snapshot directly
    snap = storage.get_snapshot(ev2_id)
    assert snap is not None
    assert len(snap["frames"]) == 2

    assert storage.get_snapshot(ev1_id) is None
    assert storage.get_snapshot(9999) is None

    # 7. Statistics
    stats = storage.get_stats()
    assert stats["total_episodes"] == 2
    assert stats["by_type"]["stimming"] == 1
    assert stats["by_type"]["avoidance"] == 1
    assert stats["by_source"]["live"] == 1
    assert stats["by_source"]["ingest"] == 1
    assert stats["snapshots_count"] == 1

    # 8. Delete event and verify cascade
    assert storage.delete_event(ev2_id) is True
    assert storage.get_episode(ev2_id) is None
    assert storage.get_snapshot(ev2_id) is None
    assert len(storage.get_episodes()) == 1


def test_event_storage_clear(tmp_path):
    db_file = str(tmp_path / "outbox.db")
    storage = EventStorage(db_path=db_file)
    storage.add_event(type="stimming", started_at="2026-09-02T12:00:00Z")
    assert len(storage.get_episodes()) == 1
    storage.clear()
    assert len(storage.get_episodes()) == 0

"""Tests for clip storage layout and the retention budget.

Stdlib only -- ClipStore's path handling and eviction touch nothing but the
filesystem, so these run without numpy or OpenCV. Actual video encoding
(write_frames, extract_clips_from_video, FrameRetentionBuffer) needs cv2 and is
NOT covered here.

Runs under pytest, and directly: `python tests/test_clip_retention.py`.
"""

import os
import sys
import tempfile
import time

from src.clip_writer import BYTES_PER_GB, ClipStore  # noqa: E402

def _make_clip(store, source_id, event_id, size_bytes, age_sec=0):
    path = store.path_for(source_id, event_id)
    with open(path, "wb") as fh:
        fh.write(b"\0" * size_bytes)
    if age_sec:
        old = time.time() - age_sec
        os.utime(path, (old, old))
    return path

def test_path_layout_groups_by_source():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        path = store.path_for("clip_01", "abc-123")
        assert path.endswith(os.path.join("clips", "clip_01", "abc-123.mp4"))
        assert os.path.isdir(os.path.dirname(path)), "the directory must be created"

def test_total_bytes_counts_every_source():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        _make_clip(store, "vid_a", "e1", 1000)
        _make_clip(store, "vid_b", "e2", 2000)
        assert store.total_bytes() == 3000

def test_budget_is_a_no_op_when_under():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"), max_total_gb=1.0)
        kept = _make_clip(store, "vid", "e1", 5000)
        deleted, freed = store.enforce_budget()
        assert (deleted, freed) == (0, 0)
        assert os.path.exists(kept)

def test_budget_evicts_oldest_first():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        store.max_total_bytes = 2500          # room for two of these, not three

        oldest = _make_clip(store, "vid", "old", 1000, age_sec=3000)
        middle = _make_clip(store, "vid", "mid", 1000, age_sec=2000)
        newest = _make_clip(store, "vid", "new", 1000, age_sec=10)

        deleted, freed = store.enforce_budget()
        assert (deleted, freed) == (1, 1000)
        assert not os.path.exists(oldest), "the oldest clip should go first"
        assert os.path.exists(middle) and os.path.exists(newest)
        assert store.total_bytes() <= store.max_total_bytes

def test_budget_evicts_repeatedly_until_under():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        store.max_total_bytes = 1200

        for i in range(5):
            _make_clip(store, "vid", "e%d" % i, 1000, age_sec=5000 - i * 100)

        deleted, _ = store.enforce_budget()
        assert deleted == 4
        assert store.total_bytes() <= store.max_total_bytes

def test_budget_tolerates_an_empty_store():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        os.makedirs(store.root, exist_ok=True)
        assert store.enforce_budget() == (0, 0)
        assert store.total_bytes() == 0

def test_gb_budget_converts_to_bytes():
    store = ClipStore("/tmp/nope", max_total_gb=5.0)
    assert store.max_total_bytes == 5 * BYTES_PER_GB

def test_relative_path_is_project_rooted():
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        path = store.path_for("vid", "e1")
        assert store.relative(path) == os.path.join("clips", "vid", "e1.mp4")

def test_non_mp4_files_are_left_alone():
    """Eviction must not touch anything it did not write."""
    with tempfile.TemporaryDirectory() as d:
        store = ClipStore(os.path.join(d, "clips"))
        store.max_total_bytes = 10
        _make_clip(store, "vid", "e1", 1000, age_sec=5000)
        notes = os.path.join(store.root, "vid", "notes.txt")
        with open(notes, "w", encoding="utf-8") as fh:
            fh.write("keep me")

        store.enforce_budget()
        assert os.path.exists(notes)

def test_relative_paths_resolve_from_the_project_root():
    """A clip_path in the event log must resolve from where the scripts run.

    Relative to the clip directory's PARENT -- the original behaviour -- a file
    at data/clips/vid/e1.mp4 was logged as clips/vid/e1.mp4, which resolves to
    nothing from the project root.
    """
    with tempfile.TemporaryDirectory() as project:
        store = ClipStore(os.path.join(project, "data", "clips"), project_root=project)
        path = store.path_for("vid", "e1")
        relative = store.relative(path)
        assert relative == os.path.join("data", "clips", "vid", "e1.mp4")
        with open(path, "wb") as fh:
            fh.write(b"x")
        assert os.path.exists(os.path.join(project, relative)), "must resolve from the project root"

if __name__ == "__main__":
    tests = sorted(
        (name, obj)
        for name, obj in list(globals().items())
        if name.startswith("test_") and callable(obj)
    )
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("  PASS  %s" % name)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print("  FAIL  %s: %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d passed, %d failed, %d total" % (len(tests) - failed, failed, len(tests)))
    sys.exit(1 if failed else 0)

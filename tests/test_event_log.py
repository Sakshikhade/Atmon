"""Tests for the event log (spec 9).

Stdlib only, like the module under test. Runs under pytest, and also directly
(`python tests/test_event_log.py`) so the log can be verified in an environment
where torch/numpy are not installed.
"""

import csv
import datetime
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.event_log import (  # noqa: E402
    COLUMNS,
    EventLogWriter,
    live_session_id,
    new_event_id,
    new_run_id,
    read_events,
    unclosed_events,
    video_start_utc,
)

ORIGIN = datetime.datetime(2026, 9, 2, 14, 0, 0, tzinfo=datetime.timezone.utc)


def _writer(path, source_type="video", source_start_utc=None, run_id=None):
    return EventLogWriter(
        path=path,
        run_id=run_id or new_run_id(),
        source_id="clip_01" if source_type == "video" else "live_20260902T140000Z",
        source_type=source_type,
        model_id="stub-encoder",
        working_fps=8,
        chunk_sec=1.0,
        tau_high=0.72,
        source_start_utc=source_start_utc,
    )


def _rows(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_header_written_once_across_runs():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path) as w:
            w.write_closed("hair_twirl", 1.0, 3.0, 0.9)
        with _writer(path) as w:
            w.write_closed("hair_twirl", 5.0, 7.0, 0.8)

        with open(path, encoding="utf-8") as fh:
            lines = [ln for ln in fh.read().splitlines() if ln.strip()]
        assert len(lines) == 3, "expected header + 2 data rows, got %d lines" % len(lines)
        assert lines[0] == ",".join(COLUMNS)
        assert "run_id" not in lines[2]


def test_column_order_matches_spec():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path) as w:
            w.write_closed("hair_twirl", 0.0, 1.0, 0.5)
        with open(path, encoding="utf-8") as fh:
            header = fh.readline().strip().split(",")
        assert header == COLUMNS


def test_offline_writes_one_closed_row():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path) as w:
            w.write_closed("hair_twirl", 42.1, 55.8, 0.813)

        rows = _rows(path)
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "closed"
        assert row["source_type"] == "video"
        assert float(row["start_sec"]) == 42.1
        assert float(row["end_sec"]) == 55.8
        assert abs(float(row["duration_sec"]) - 13.7) < 1e-6
        assert row["tau_high"] == "0.720000"
        assert row["model_id"] == "stub-encoder"


def test_offline_writer_refuses_open_rows():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path) as w:
            try:
                w.open_event(new_event_id(), "hair_twirl", 1.0, 0.9)
            except RuntimeError:
                pass
            else:
                raise AssertionError("offline writer must refuse to write an open row")


def test_live_open_then_close_shares_event_id():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        eid = new_event_id()
        with _writer(path, "live", ORIGIN) as w:
            w.open_event(eid, "hair_twirl", 10.0, 0.77)
            w.close_event(eid, "hair_twirl", 10.0, 18.5, 0.81)

        rows = _rows(path)
        assert len(rows) == 2
        assert rows[0]["status"] == "open"
        assert rows[1]["status"] == "closed"
        assert rows[0]["event_id"] == rows[1]["event_id"] == eid
        # The open row must carry a start and no end.
        assert rows[0]["end_sec"] == ""
        assert rows[0]["end_utc"] == ""
        assert rows[0]["duration_sec"] == ""
        assert float(rows[0]["start_sec"]) == 10.0


def test_read_events_takes_last_row_per_event():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        eid = new_event_id()
        with _writer(path, "live", ORIGIN) as w:
            w.open_event(eid, "hair_twirl", 10.0, 0.77)
            w.close_event(eid, "hair_twirl", 10.0, 18.5, 0.81)

        events = read_events(path)
        assert len(events) == 1, "open+closed must collapse to one event"
        ev = events[0]
        assert ev["status"] == "closed"
        assert ev["end_sec"] == 18.5
        assert ev["score"] == 0.81
        assert not unclosed_events(path)


def test_unclosed_event_survives_as_open():
    """A run that died mid-detection leaves a real start and no end."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        eid = new_event_id()
        with _writer(path, "live", ORIGIN) as w:
            w.open_event(eid, "hair_twirl", 10.0, 0.77)

        events = read_events(path)
        assert len(events) == 1
        assert events[0]["status"] == "open"
        assert events[0]["end_sec"] is None
        assert events[0]["start_sec"] == 10.0

        still_open = unclosed_events(path)
        assert len(still_open) == 1 and still_open[0]["event_id"] == eid


def test_wall_clock_derived_from_origin():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path, "live", ORIGIN) as w:
            w.write_closed("hair_twirl", 90.0, 150.5, 0.9)

        ev = read_events(path)[0]
        assert ev["start_utc"] == ORIGIN + datetime.timedelta(seconds=90.0)
        assert ev["end_utc"] == ORIGIN + datetime.timedelta(seconds=150.5)


def test_unknown_origin_leaves_wall_clock_empty():
    """Never fabricate a datetime -- an empty cell is the honest answer."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path, "video", None) as w:
            w.write_closed("hair_twirl", 90.0, 150.5, 0.9)

        row = _rows(path)[0]
        assert row["start_utc"] == ""
        assert row["end_utc"] == ""
        # ...but the relative times, which evaluation uses, are still there.
        assert float(row["start_sec"]) == 90.0
        assert read_events(path)[0]["start_utc"] is None


def test_video_start_utc_opt_in_only():
    with tempfile.TemporaryDirectory() as d:
        video = os.path.join(d, "x.mp4")
        with open(video, "wb") as fh:
            fh.write(b"not really a video")
        assert video_start_utc(video, infer_from_mtime=False) is None
        assert video_start_utc(video, infer_from_mtime=True) is not None
        assert video_start_utc(os.path.join(d, "missing.mp4"), True) is None


def test_runs_are_distinguishable_in_one_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        r1, r2 = new_run_id(), new_run_id()
        with _writer(path, run_id=r1) as w:
            w.write_closed("hair_twirl", 1.0, 2.0, 0.9)
        with _writer(path, run_id=r2) as w:
            w.write_closed("hair_twirl", 1.0, 2.0, 0.9)

        rows = _rows(path)
        assert len(rows) == 2
        assert {rows[0]["run_id"], rows[1]["run_id"]} == {r1, r2}
        # Same detection, different runs -> distinct events, not deduplicated.
        assert rows[0]["event_id"] != rows[1]["event_id"]


def test_clip_path_round_trips():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path) as w:
            w.write_closed("hair_twirl", 1.0, 3.0, 0.9, clip_path="clips/clip_01/abc.mp4")
        ev = read_events(path)[0]
        assert ev["clip_path"] == "clips/clip_01/abc.mp4"


def test_clip_path_absent_reads_as_none():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        with _writer(path) as w:
            w.write_closed("hair_twirl", 1.0, 3.0, 0.9)
        assert read_events(path)[0]["clip_path"] is None


def test_open_row_carries_no_clip_path():
    """A clip cannot exist before the event's extent is known."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "events.csv")
        eid = new_event_id()
        with _writer(path, "live", ORIGIN) as w:
            w.open_event(eid, "hair_twirl", 10.0, 0.77)
            w.close_event(eid, "hair_twirl", 10.0, 18.5, 0.81, clip_path="clips/s/abc.mp4")

        rows = _rows(path)
        assert rows[0]["clip_path"] == ""
        assert rows[1]["clip_path"] == "clips/s/abc.mp4"
        assert read_events(path)[0]["clip_path"] == "clips/s/abc.mp4"


def test_reads_a_log_written_before_clips_existed():
    """16-column logs predate the clip_path column and must still load."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "old.csv")
        legacy = [c for c in COLUMNS if c != "clip_path"]
        with open(path, "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=legacy)
            writer.writeheader()
            writer.writerow(
                {
                    "run_id": "r1", "event_id": "e1", "source_id": "clip_01",
                    "source_type": "video", "status": "closed", "class": "hair_twirl",
                    "start_sec": "1.000", "end_sec": "3.000", "duration_sec": "2.000",
                    "start_utc": "", "end_utc": "", "score": "0.900000",
                    "model_id": "m", "working_fps": "8", "chunk_sec": "2",
                    "tau_high": "0.7", "written_utc": "2026-09-02T14:00:00Z",
                }
            )
        events = read_events(path)
        assert len(events) == 1 and events[0]["clip_path"] is None


def test_live_session_id_format():
    sid = live_session_id(ORIGIN)
    assert sid == "live_20260902T140000Z"


def test_empty_log_reads_as_no_events():
    with tempfile.TemporaryDirectory() as d:
        assert read_events(os.path.join(d, "nope.csv")) == []


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

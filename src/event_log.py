"""Durable CSV event log (spec 9).

Append-only. Never rewrites a row. Both detect.py (offline) and detect_live.py
(streaming) write here through the same writer, so the two paths cannot drift.

Deliberately depends on the standard library only -- this module is the system's
only durable output and must keep working when torch/numpy are broken or absent.
"""

import csv
import datetime
import os
import uuid

# Exactly the columns from spec 4, in that order. Do not reorder or rename.
COLUMNS = [
    "run_id",
    "event_id",
    "source_id",
    "source_type",
    "status",
    "class",
    "start_sec",
    "end_sec",
    "duration_sec",
    "start_utc",
    "end_utc",
    "score",
    "model_id",
    "working_fps",
    "chunk_sec",
    "tau_high",
    "written_utc",
    "clip_path",
]

STATUS_OPEN = "open"
STATUS_CLOSED = "closed"

SOURCE_VIDEO = "video"
SOURCE_LIVE = "live"


def utc_now():
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt):
    """ISO-8601 with a trailing Z, or "" for None."""
    if dt is None:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(text):
    """Inverse of iso(). Returns None for the empty string."""
    if not text:
        return None
    return datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))


def new_run_id():
    return str(uuid.uuid4())


def new_event_id():
    return str(uuid.uuid4())


def live_session_id(started_at=None):
    """Session id for a camera feed: live_<YYYYMMDD>T<HHMMSS>Z (spec 10.4)."""
    started_at = started_at or utc_now()
    return started_at.astimezone(datetime.timezone.utc).strftime("live_%Y%m%dT%H%M%SZ")


def video_start_utc(video_path, infer_from_mtime):
    """Wall-clock origin for a recorded file (spec 9.3).

    Returns None when unknown -- callers must then leave start_utc/end_utc empty
    rather than substituting the processing time.
    """
    if not infer_from_mtime:
        return None
    try:
        mtime = os.path.getmtime(video_path)
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(mtime, datetime.timezone.utc)


class EventLogWriter:
    """Append-only writer for one run.

    Offline callers use write_closed() only. Live callers use open_event() at
    hysteresis open and close_event() at close -- two rows sharing one event_id,
    never an edit to the first row (spec 9.1).
    """

    def __init__(
        self,
        path,
        run_id,
        source_id,
        source_type,
        model_id,
        working_fps,
        chunk_sec,
        tau_high,
        source_start_utc=None,
        flush_each_event=True,
    ):
        if source_type not in (SOURCE_VIDEO, SOURCE_LIVE):
            raise ValueError("source_type must be %r or %r" % (SOURCE_VIDEO, SOURCE_LIVE))

        self.path = path
        self.run_id = run_id
        self.source_id = source_id
        self.source_type = source_type
        self.model_id = model_id
        self.working_fps = working_fps
        self.chunk_sec = chunk_sec
        self.tau_high = tau_high
        self.source_start_utc = source_start_utc
        self.flush_each_event = flush_each_event

        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)

        # Header only when creating the file; appending must not repeat it.
        needs_header = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=COLUMNS)
        if needs_header:
            self._writer.writeheader()
            self._sync()

    # -- internals ---------------------------------------------------------

    def _sync(self):
        self._fh.flush()
        if self.flush_each_event:
            os.fsync(self._fh.fileno())

    def _abs(self, offset_sec):
        """Absolute time for an offset, or "" when the origin is unknown.

        Never fabricates: an unknown origin yields an empty cell, not the
        processing time and not the epoch (spec 9.3).
        """
        if self.source_start_utc is None or offset_sec is None:
            return ""
        return iso(self.source_start_utc + datetime.timedelta(seconds=float(offset_sec)))

    def _row(self, event_id, status, cls, start_sec, end_sec, score, clip_path=None):
        duration = None if end_sec is None else float(end_sec) - float(start_sec)
        return {
            "run_id": self.run_id,
            "event_id": event_id,
            "source_id": self.source_id,
            "source_type": self.source_type,
            "status": status,
            "class": cls,
            "start_sec": "%.3f" % float(start_sec),
            "end_sec": "" if end_sec is None else "%.3f" % float(end_sec),
            "duration_sec": "" if duration is None else "%.3f" % duration,
            "start_utc": self._abs(start_sec),
            "end_utc": "" if end_sec is None else self._abs(end_sec),
            "score": "%.6f" % float(score),
            "model_id": self.model_id,
            "working_fps": "%g" % float(self.working_fps),
            "chunk_sec": "%g" % float(self.chunk_sec),
            "tau_high": "" if self.tau_high is None else "%.6f" % float(self.tau_high),
            "written_utc": iso(utc_now()),
            "clip_path": clip_path or "",
        }

    def _append(self, row):
        self._writer.writerow(row)
        self._sync()
        return row

    # -- public API --------------------------------------------------------

    def open_event(self, event_id, cls, start_sec, score):
        """Provisional row for a detection whose end is not yet known.

        Live mode only -- offline grouping always knows both boundaries, so
        writing an open row there would be a bug (spec 9.1). clip_path is always
        empty here: a clip cannot be written until the event's extent is known.
        """
        if self.source_type != SOURCE_LIVE:
            raise RuntimeError("open rows are live-only; offline runs write closed rows only")
        return self._append(self._row(event_id, STATUS_OPEN, cls, start_sec, None, score))

    def close_event(self, event_id, cls, start_sec, end_sec, score, clip_path=None):
        """Final row. Appended, never written over the open row."""
        return self._append(
            self._row(event_id, STATUS_CLOSED, cls, start_sec, end_sec, score, clip_path)
        )

    def write_closed(self, cls, start_sec, end_sec, score, event_id=None, clip_path=None):
        """One complete detection. The offline path's only entry point."""
        event_id = event_id or new_event_id()
        self.close_event(event_id, cls, start_sec, end_sec, score, clip_path)
        return event_id

    def close(self):
        if self._fh is not None:
            self._fh.flush()
            self._fh.close()
            self._fh = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _to_float(text):
    return None if text == "" else float(text)


def read_events(path):
    """Read the log and collapse it to final event state (spec 9.2).

    Groups rows by event_id and keeps the LAST row for each. An event whose last
    row is status "open" ended with the run -- cleanly or by crash -- and has a
    real start and no end. Callers must handle that explicitly rather than
    dropping it or reading end_sec as zero.

    Returns events in first-appearance order.
    """
    if not os.path.exists(path):
        return []

    final = {}
    order = []
    with open(path, "r", newline="", encoding="utf-8") as fh:
        for raw in csv.DictReader(fh):
            event_id = raw["event_id"]
            if event_id not in final:
                order.append(event_id)
            final[event_id] = {
                "run_id": raw["run_id"],
                "event_id": event_id,
                "source_id": raw["source_id"],
                "source_type": raw["source_type"],
                "status": raw["status"],
                "class": raw["class"],
                "start_sec": _to_float(raw["start_sec"]),
                "end_sec": _to_float(raw["end_sec"]),
                "duration_sec": _to_float(raw["duration_sec"]),
                "start_utc": parse_iso(raw["start_utc"]),
                "end_utc": parse_iso(raw["end_utc"]),
                "score": _to_float(raw["score"]),
                "model_id": raw["model_id"],
                "working_fps": _to_float(raw["working_fps"]),
                "chunk_sec": _to_float(raw["chunk_sec"]),
                "tau_high": _to_float(raw["tau_high"]),
                "written_utc": parse_iso(raw["written_utc"]),
                # .get(): logs written before clips existed have 16 columns.
                # A path here records where the clip WAS written; retention may
                # since have deleted it, so check the file before opening it.
                "clip_path": raw.get("clip_path") or None,
            }
    return [final[e] for e in order]


def unclosed_events(path):
    """Events left open when their run ended. Never silently discard these."""
    return [e for e in read_events(path) if e["status"] == STATUS_OPEN]

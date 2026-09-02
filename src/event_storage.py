"""Local SQLite storage for behavior events and skeleton replay snapshots.

Detected events from live camera monitoring and recorded video ingestion are
persisted locally in `data/outbox.db`.

Privacy-by-Design:
- Only metadata (timestamps, duration, behavior type) and normalized 3D skeletal
  coordinates for 11 key joints are stored.
- Raw video frames, pixel data, and facial imagery are never stored or transmitted.
"""

import datetime
import json
import os
import sqlite3
import threading
from collections import deque

from src.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_DB_PATH = "data/outbox.db"

# MediaPipe landmark indices captured for skeleton replay snapshots:
# 0: nose, 2: left-eye, 5: right-eye
# 11: left-shoulder, 12: right-shoulder
# 13: left-elbow, 14: right-elbow
# 15: left-wrist, 16: right-wrist
# 23: left-hip, 24: right-hip
SNAPSHOT_JOINTS = (0, 2, 5, 11, 12, 13, 14, 15, 16, 23, 24)

# Replay snapshot parameters
SNAPSHOT_FPS = 10.0
SNAPSHOT_MAX_DURATION_S = 2.0


def utcnow_iso() -> str:
    """Return current UTC time in ISO-8601 format."""
    return datetime.datetime.now(datetime.UTC).isoformat()


def build_snapshot(
    landmark_buffer: deque | list,
    started_at: float,
    ended_at: float,
    snapshot_fps: float = SNAPSHOT_FPS,
    max_duration_s: float = SNAPSHOT_MAX_DURATION_S,
    joints: tuple = SNAPSHOT_JOINTS,
) -> dict | None:
    """Build a skeleton-replay snapshot dictionary from a buffer of pose landmarks.

    Slices landmark frames within `[started_at, ended_at]` (capped to `max_duration_s`),
    downsamples to `snapshot_fps`, and extracts normalized numeric coordinates for
    the specified joint indices.

    Args:
        landmark_buffer: Collection of `(timestamp: float, pose_landmarks)` tuples.
        started_at: Episode start timestamp (epoch seconds).
        ended_at: Episode resolution timestamp (epoch seconds).
        snapshot_fps: Desired sampling rate for animation playback (default 10.0).
        max_duration_s: Maximum time window to include in snapshot (default 2.0s).
        joints: Tuple of landmark integer indices to retain.

    Returns:
        Dict format `{"fps": float, "frames": [{"t": float, "points": [{"i": int, "x": float, "y": float, "z": float}]}]}`
        or None if no valid landmark frames exist in the window.
    """
    if not landmark_buffer or started_at is None or ended_at is None:
        return None

    capture_start = max(started_at, ended_at - max_duration_s)
    window = [(t, lm) for t, lm in landmark_buffer if lm and capture_start <= t <= ended_at]
    if not window:
        return None

    interval = 1.0 / snapshot_fps if snapshot_fps > 0 else 0.1
    selected: list[tuple[float, list]] = []
    last_kept_t = -1.0
    for t, lm in window:
        if last_kept_t < 0 or (t - last_kept_t) >= interval:
            selected.append((t, lm))
            last_kept_t = t

    if not selected:
        return None

    t0 = selected[0][0]
    frames = []
    for t, lm in selected:
        points = []
        for idx in joints:
            if idx < len(lm):
                pt = lm[idx]
                points.append(
                    {
                        "i": idx,
                        "x": round(float(pt.x), 4),
                        "y": round(float(pt.y), 4),
                        "z": round(float(pt.z), 4),
                    }
                )
        if points:
            frames.append(
                {
                    "t": round(t - t0, 3),
                    "points": points,
                }
            )

    if not frames:
        return None

    return {
        "fps": snapshot_fps,
        "frames": frames,
    }


class EventStorage:
    """Thread-safe SQLite storage for behavior events and skeleton snapshots."""

    def __init__(self, db_path: str = DEFAULT_DB_PATH):
        self.db_path = db_path
        self._lock = threading.Lock()

        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)

        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            # Check for legacy tables/columns from prior iterations
            cols = [
                row[1]
                for row in conn.execute("PRAGMA table_info(episode_snapshots)").fetchall()
            ]
            if cols and "event_id" not in cols:
                conn.execute("DROP TABLE IF EXISTS episode_snapshots")
                conn.execute("DROP TABLE IF EXISTS outbox")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    type        TEXT NOT NULL,
                    subtype     TEXT,
                    action      TEXT,
                    started_at  TEXT NOT NULL,
                    ended_at    TEXT,
                    duration_s  REAL,
                    intensity   REAL,
                    source      TEXT NOT NULL DEFAULT 'live',
                    created_at  TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_started_at ON events (started_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events (type)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_source ON events (source)")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS episode_snapshots (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id    INTEGER NOT NULL REFERENCES events (id) ON DELETE CASCADE,
                    fps         REAL NOT NULL DEFAULT 10.0,
                    frames      TEXT NOT NULL,  -- JSON: [{t, points:[{i,x,y,z}]}]
                    created_at  TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_snapshots_event_id ON episode_snapshots (event_id)"
            )

    def add_event(
        self,
        type: str,
        started_at: str,
        subtype: str | None = None,
        action: str | None = None,
        ended_at: str | None = None,
        duration_s: float | None = None,
        intensity: float | None = None,
        source: str = "live",
        snapshot: dict | None = None,
    ) -> int:
        """Add a detected behavior event and optional skeleton snapshot.

        Returns the newly created event ID.
        """
        now = utcnow_iso()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO events (type, subtype, action, started_at, ended_at, duration_s, intensity, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    type,
                    subtype,
                    action,
                    started_at,
                    ended_at,
                    duration_s,
                    intensity,
                    source,
                    now,
                ),
            )
            event_id = cur.lastrowid

            if snapshot and "frames" in snapshot and snapshot["frames"]:
                fps = float(snapshot.get("fps", SNAPSHOT_FPS))
                frames_json = json.dumps(snapshot["frames"])
                conn.execute(
                    """
                    INSERT INTO episode_snapshots (event_id, fps, frames, created_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (event_id, fps, frames_json, now),
                )

            return event_id

    def get_episodes(
        self,
        date: str | None = None,
        from_time: str | None = None,
        to_time: str | None = None,
        behavior_type: str | None = None,
        source: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query episodes with optional date/time range and type filters."""
        query = """
            SELECT e.*,
                   CASE WHEN s.id IS NOT NULL THEN 1 ELSE 0 END AS has_snapshot,
                   s.fps AS snapshot_fps
            FROM events e
            LEFT JOIN episode_snapshots s ON e.id = s.event_id
            WHERE 1=1
        """
        params: list = []

        if date:
            query += " AND substr(e.started_at, 1, 10) = ?"
            params.append(date)
        if from_time:
            # Handles either HH:MM or full ISO timestamp
            if len(from_time) <= 5:
                query += " AND substr(e.started_at, 12, 5) >= ?"
            else:
                query += " AND e.started_at >= ?"
            params.append(from_time)
        if to_time:
            if len(to_time) <= 5:
                query += " AND substr(e.started_at, 12, 5) <= ?"
            else:
                query += " AND e.started_at <= ?"
            params.append(to_time)
        if behavior_type:
            query += " AND (e.type = ? OR e.subtype = ?)"
            params.extend([behavior_type, behavior_type])
        if source:
            query += " AND e.source = ?"
            params.append(source)

        query += " ORDER BY e.started_at DESC, e.id DESC LIMIT ?"
        params.append(limit)

        with self._lock, self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
            return [dict(r) for r in rows]

    def get_episode(self, event_id: int) -> dict | None:
        """Fetch a single episode by ID, including its parsed skeleton snapshot if present."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT e.*,
                       s.id AS snapshot_id,
                       s.fps AS snapshot_fps,
                       s.frames AS snapshot_frames
                FROM events e
                LEFT JOIN episode_snapshots s ON e.id = s.event_id
                WHERE e.id = ?
                """,
                (event_id,),
            ).fetchone()

            if not row:
                return None

            data = dict(row)
            if data.get("snapshot_frames"):
                try:
                    data["snapshot"] = {
                        "fps": data.get("snapshot_fps", SNAPSHOT_FPS),
                        "frames": json.loads(data["snapshot_frames"]),
                    }
                except Exception as e:
                    logger.warning("Failed to decode snapshot JSON for event %s: %s", event_id, e)
                    data["snapshot"] = None
            else:
                data["snapshot"] = None

            data.pop("snapshot_frames", None)
            return data

    def get_snapshot(self, event_id: int) -> dict | None:
        """Fetch only the skeleton snapshot dictionary for a given event ID."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT fps, frames FROM episode_snapshots WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if not row:
                return None
            try:
                return {
                    "fps": row["fps"],
                    "frames": json.loads(row["frames"]),
                }
            except Exception as e:
                logger.warning("Failed to decode snapshot JSON for event %s: %s", event_id, e)
                return None

    def get_stats(self) -> dict:
        """Return summary statistics across all recorded events."""
        with self._lock, self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) AS count FROM events").fetchone()["count"]

            today = datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
            today_count = conn.execute(
                "SELECT COUNT(*) AS count FROM events WHERE substr(started_at, 1, 10) = ?",
                (today,),
            ).fetchone()["count"]

            by_type = {}
            for r in conn.execute(
                "SELECT type, COUNT(*) AS count FROM events GROUP BY type"
            ).fetchall():
                by_type[r["type"]] = r["count"]

            by_subtype = {}
            for r in conn.execute(
                "SELECT subtype, COUNT(*) AS count FROM events WHERE subtype IS NOT NULL GROUP BY subtype"
            ).fetchall():
                by_subtype[r["subtype"]] = r["count"]

            by_source = {}
            for r in conn.execute(
                "SELECT source, COUNT(*) AS count FROM events GROUP BY source"
            ).fetchall():
                by_source[r["source"]] = r["count"]

            snapshots_count = conn.execute(
                "SELECT COUNT(*) AS count FROM episode_snapshots"
            ).fetchone()["count"]

            return {
                "total_episodes": total,
                "today_episodes": today_count,
                "by_type": by_type,
                "by_subtype": by_subtype,
                "by_source": by_source,
                "snapshots_count": snapshots_count,
            }

    def delete_event(self, event_id: int) -> bool:
        """Delete an event and its linked snapshot."""
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
            return cur.rowcount > 0

    def clear(self) -> None:
        """Clear all events and snapshots (useful for tests or local reset)."""
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM episode_snapshots")
            conn.execute("DELETE FROM events")

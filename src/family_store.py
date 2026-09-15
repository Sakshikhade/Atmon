"""SQLite storage for the family app — accounts, consent, recordings, clinician shares.

Privacy-by-Design:
- No raw video stored; recordings reference behavior event IDs from event_storage.
- Clinician shares use opaque tokens; no PII leaked in share URLs.
- All deletes are soft-deleted (deleted_at) so retention policy can enforce hard deletion.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import threading
from datetime import datetime, UTC, timedelta

from src.logging_config import get_logger

logger = get_logger(__name__)

DEFAULT_FAMILY_DB = "data/family.db"

# Feature flag: Phase-2 home-camera UI
FEATURE_HOME_CAMERA_PHASE2 = os.getenv("FEATURE_HOME_CAMERA_PHASE2", "false").lower() == "true"

SESSION_TTL_HOURS = 24
VALID_ROLES = {"guardian", "caregiver", "clinician_viewer"}
VALID_CONSENT_TYPES = {"primary_consent", "assent", "bystander_acknowledged"}
VALID_CONSENT_STATUSES = {"pending", "active", "withdrawn"}


def _utcnow() -> str:
    return datetime.now(UTC).isoformat()


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}:{pin}".encode()).hexdigest()


class FamilyStore:
    """Thread-safe SQLite storage for family app entities."""

    def __init__(self, db_path: str = DEFAULT_FAMILY_DB):
        self.db_path = db_path
        self._lock = threading.Lock()
        parent = os.path.dirname(db_path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._init_db()
        self._seed_demo_household()

    # ── Connection ──────────────────────────────────────────────────────────

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    # ── Schema ───────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        with self._lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS households (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    name       TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS family_accounts (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id INTEGER NOT NULL REFERENCES households(id),
                    name         TEXT NOT NULL,
                    email        TEXT NOT NULL UNIQUE,
                    role         TEXT NOT NULL DEFAULT 'guardian',
                    pin_hash     TEXT,
                    pin_salt     TEXT,
                    created_at   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_accounts_email ON family_accounts (email);

                CREATE TABLE IF NOT EXISTS sessions (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL REFERENCES family_accounts(id) ON DELETE CASCADE,
                    token      TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions (token);

                CREATE TABLE IF NOT EXISTS consent_records (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id         INTEGER NOT NULL REFERENCES households(id),
                    type                 TEXT NOT NULL,
                    status               TEXT NOT NULL DEFAULT 'pending',
                    granted_by_account_id INTEGER REFERENCES family_accounts(id),
                    granted_at           TEXT,
                    updated_at           TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS recordings (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id INTEGER NOT NULL REFERENCES households(id),
                    label        TEXT,
                    started_at   TEXT NOT NULL,
                    ended_at     TEXT,
                    duration_s   REAL,
                    event_ids    TEXT NOT NULL DEFAULT '[]',
                    created_at   TEXT NOT NULL,
                    deleted_at   TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_recordings_household ON recordings (household_id);

                CREATE TABLE IF NOT EXISTS clinician_shares (
                    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id         INTEGER NOT NULL REFERENCES households(id),
                    recording_id         INTEGER REFERENCES recordings(id),
                    clinician_name       TEXT NOT NULL,
                    clinician_email      TEXT NOT NULL,
                    share_token          TEXT NOT NULL UNIQUE,
                    notes                TEXT,
                    shared_by_account_id INTEGER REFERENCES family_accounts(id),
                    created_at           TEXT NOT NULL,
                    expires_at           TEXT,
                    revoked_at           TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_shares_token ON clinician_shares (share_token);
                CREATE INDEX IF NOT EXISTS idx_shares_household ON clinician_shares (household_id);

                CREATE TABLE IF NOT EXISTS privacy_settings (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    household_id          INTEGER NOT NULL UNIQUE REFERENCES households(id),
                    retention_days        INTEGER NOT NULL DEFAULT 90,
                    auto_delete_enabled   INTEGER NOT NULL DEFAULT 1,
                    allow_clinician_replay INTEGER NOT NULL DEFAULT 1,
                    updated_at            TEXT NOT NULL
                );
                """
            )

    def _seed_demo_household(self) -> None:
        """Create a demo household and guardian account if none exist (first run)."""
        with self._lock, self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS c FROM households").fetchone()["c"]
            if count > 0:
                return

            now = _utcnow()
            hh_id = conn.execute(
                "INSERT INTO households (name, created_at) VALUES (?, ?)",
                ("Demo Family", now),
            ).lastrowid

            salt = secrets.token_hex(16)
            pin_hash = _hash_pin("1234", salt)
            conn.execute(
                """
                INSERT INTO family_accounts (household_id, name, email, role, pin_hash, pin_salt, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (hh_id, "Guardian", "guardian@family.local", "guardian", pin_hash, salt, now),
            )

            # Seed default consent records (all pending)
            for ctype in VALID_CONSENT_TYPES:
                conn.execute(
                    """
                    INSERT INTO consent_records (household_id, type, status, updated_at)
                    VALUES (?, ?, 'pending', ?)
                    """,
                    (hh_id, ctype, now),
                )

            # Seed default privacy settings
            conn.execute(
                """
                INSERT INTO privacy_settings (household_id, retention_days, auto_delete_enabled,
                                              allow_clinician_replay, updated_at)
                VALUES (?, 90, 1, 1, ?)
                """,
                (hh_id, now),
            )

    # ── Auth ─────────────────────────────────────────────────────────────────

    def login(self, email: str, pin: str) -> dict | None:
        """Authenticate with email + PIN; returns {token, account} or None."""
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM family_accounts WHERE email = ?", (email,)
            ).fetchone()
            if not row:
                return None
            stored_hash = row["pin_hash"]
            salt = row["pin_salt"]
            if not stored_hash or _hash_pin(pin, salt) != stored_hash:
                return None

            token = secrets.token_hex(32)
            now = _utcnow()
            expires = (datetime.now(UTC) + timedelta(hours=SESSION_TTL_HOURS)).isoformat()
            conn.execute(
                "INSERT INTO sessions (account_id, token, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (row["id"], token, now, expires),
            )
            return {"token": token, "account": _account_dict(row)}

    def resolve_session(self, token: str) -> dict | None:
        """Return account dict for a valid, non-expired session token."""
        with self._lock, self._connect() as conn:
            now = _utcnow()
            row = conn.execute(
                """
                SELECT a.* FROM sessions s
                JOIN family_accounts a ON s.account_id = a.id
                WHERE s.token = ? AND s.expires_at > ?
                """,
                (token, now),
            ).fetchone()
            return _account_dict(row) if row else None

    def logout(self, token: str) -> bool:
        with self._lock, self._connect() as conn:
            cur = conn.execute("DELETE FROM sessions WHERE token = ?", (token,))
            return cur.rowcount > 0

    # ── Household ────────────────────────────────────────────────────────────

    def get_household(self, household_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM households WHERE id = ?", (household_id,)
            ).fetchone()
            if not row:
                return None
            members = conn.execute(
                "SELECT * FROM family_accounts WHERE household_id = ?", (household_id,)
            ).fetchall()
            return {
                "id": row["id"],
                "name": row["name"],
                "created_at": row["created_at"],
                "members": [_account_dict(m) for m in members],
            }

    # ── Consent ──────────────────────────────────────────────────────────────

    def get_consent(self, household_id: int) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM consent_records WHERE household_id = ? ORDER BY type",
                (household_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def update_consent(self, household_id: int, consent_type: str, status: str, account_id: int) -> dict | None:
        if consent_type not in VALID_CONSENT_TYPES or status not in VALID_CONSENT_STATUSES:
            return None
        now = _utcnow()
        granted_at = now if status == "active" else None
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE consent_records
                SET status = ?, granted_by_account_id = ?, granted_at = ?, updated_at = ?
                WHERE household_id = ? AND type = ?
                """,
                (status, account_id, granted_at, now, household_id, consent_type),
            )
            row = conn.execute(
                "SELECT * FROM consent_records WHERE household_id = ? AND type = ?",
                (household_id, consent_type),
            ).fetchone()
            return dict(row) if row else None

    # ── Recordings ───────────────────────────────────────────────────────────

    def create_recording(self, household_id: int, label: str | None, started_at: str) -> int:
        now = _utcnow()
        with self._lock, self._connect() as conn:
            return conn.execute(
                """
                INSERT INTO recordings (household_id, label, started_at, event_ids, created_at)
                VALUES (?, ?, ?, '[]', ?)
                """,
                (household_id, label, started_at, now),
            ).lastrowid

    def stop_recording(self, recording_id: int, ended_at: str, event_ids: list[int]) -> bool:
        started = self._get_recording_started_at(recording_id)
        if not started:
            return False
        try:
            t0 = datetime.fromisoformat(started.replace("Z", "+00:00"))
            t1 = datetime.fromisoformat(ended_at.replace("Z", "+00:00"))
            duration = (t1 - t0).total_seconds()
        except Exception:
            duration = None

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE recordings SET ended_at = ?, duration_s = ?, event_ids = ?
                WHERE id = ? AND deleted_at IS NULL
                """,
                (ended_at, duration, json.dumps(event_ids), recording_id),
            )
            return True

    def _get_recording_started_at(self, recording_id: int) -> str | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT started_at FROM recordings WHERE id = ?", (recording_id,)
            ).fetchone()
            return row["started_at"] if row else None

    def get_recordings(self, household_id: int, include_deleted: bool = False) -> list[dict]:
        with self._lock, self._connect() as conn:
            query = "SELECT * FROM recordings WHERE household_id = ?"
            params = [household_id]
            if not include_deleted:
                query += " AND deleted_at IS NULL"
            query += " ORDER BY started_at DESC"
            rows = conn.execute(query, params).fetchall()
            return [_recording_dict(r) for r in rows]

    def get_recording(self, recording_id: int, household_id: int | None = None) -> dict | None:
        with self._lock, self._connect() as conn:
            query = "SELECT * FROM recordings WHERE id = ? AND deleted_at IS NULL"
            params = [recording_id]
            if household_id is not None:
                query += " AND household_id = ?"
                params.append(household_id)
            row = conn.execute(query, params).fetchone()
            return _recording_dict(row) if row else None

    def delete_recording(self, recording_id: int, household_id: int) -> bool:
        now = _utcnow()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE recordings SET deleted_at = ? WHERE id = ? AND household_id = ? AND deleted_at IS NULL",
                (now, recording_id, household_id),
            )
            return cur.rowcount > 0

    # ── Clinician Shares ─────────────────────────────────────────────────────

    def create_share(
        self,
        household_id: int,
        recording_id: int | None,
        clinician_name: str,
        clinician_email: str,
        account_id: int,
        notes: str | None = None,
        expires_days: int | None = None,
    ) -> dict:
        token = secrets.token_urlsafe(24)
        now = _utcnow()
        expires_at = None
        if expires_days:
            expires_at = (datetime.now(UTC) + timedelta(days=expires_days)).isoformat()

        with self._lock, self._connect() as conn:
            share_id = conn.execute(
                """
                INSERT INTO clinician_shares
                    (household_id, recording_id, clinician_name, clinician_email, share_token,
                     notes, shared_by_account_id, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (household_id, recording_id, clinician_name, clinician_email,
                 token, notes, account_id, now, expires_at),
            ).lastrowid
            row = conn.execute(
                "SELECT * FROM clinician_shares WHERE id = ?", (share_id,)
            ).fetchone()
            return dict(row)

    def get_shares(self, household_id: int) -> list[dict]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM clinician_shares
                WHERE household_id = ? AND revoked_at IS NULL
                ORDER BY created_at DESC
                """,
                (household_id,),
            ).fetchall()
            return [dict(r) for r in rows]

    def revoke_share(self, share_id: int, household_id: int) -> bool:
        now = _utcnow()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "UPDATE clinician_shares SET revoked_at = ? WHERE id = ? AND household_id = ? AND revoked_at IS NULL",
                (now, share_id, household_id),
            )
            return cur.rowcount > 0

    def get_share_by_token(self, token: str) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM clinician_shares WHERE share_token = ? AND revoked_at IS NULL",
                (token,),
            ).fetchone()
            if not row:
                return None
            share = dict(row)
            now = _utcnow()
            if share.get("expires_at") and share["expires_at"] < now:
                return None
            return share

    # ── Privacy Settings ─────────────────────────────────────────────────────

    def get_privacy_settings(self, household_id: int) -> dict | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM privacy_settings WHERE household_id = ?", (household_id,)
            ).fetchone()
            return dict(row) if row else None

    def update_privacy_settings(
        self,
        household_id: int,
        retention_days: int | None = None,
        auto_delete_enabled: bool | None = None,
        allow_clinician_replay: bool | None = None,
    ) -> dict | None:
        current = self.get_privacy_settings(household_id)
        if not current:
            return None
        now = _utcnow()
        new_retention = retention_days if retention_days is not None else current["retention_days"]
        new_auto = int(auto_delete_enabled) if auto_delete_enabled is not None else current["auto_delete_enabled"]
        new_replay = int(allow_clinician_replay) if allow_clinician_replay is not None else current["allow_clinician_replay"]

        with self._lock, self._connect() as conn:
            conn.execute(
                """
                UPDATE privacy_settings
                SET retention_days = ?, auto_delete_enabled = ?, allow_clinician_replay = ?, updated_at = ?
                WHERE household_id = ?
                """,
                (new_retention, new_auto, new_replay, now, household_id),
            )
            row = conn.execute(
                "SELECT * FROM privacy_settings WHERE household_id = ?", (household_id,)
            ).fetchone()
            return dict(row) if row else None

    # ── Retention cleanup ────────────────────────────────────────────────────

    def apply_retention_policy(self, household_id: int) -> int:
        """Hard-delete soft-deleted recordings older than retention_days. Returns count deleted."""
        settings = self.get_privacy_settings(household_id)
        if not settings or not settings.get("auto_delete_enabled"):
            return 0
        cutoff = (datetime.now(UTC) - timedelta(days=settings["retention_days"])).isoformat()
        with self._lock, self._connect() as conn:
            cur = conn.execute(
                "DELETE FROM recordings WHERE household_id = ? AND deleted_at IS NOT NULL AND deleted_at < ?",
                (household_id, cutoff),
            )
            return cur.rowcount


# ── Helpers ──────────────────────────────────────────────────────────────────

def _account_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    d.pop("pin_hash", None)
    d.pop("pin_salt", None)
    return d


def _recording_dict(row: sqlite3.Row) -> dict:
    d = dict(row)
    try:
        d["event_ids"] = json.loads(d.get("event_ids") or "[]")
    except Exception:
        d["event_ids"] = []
    return d

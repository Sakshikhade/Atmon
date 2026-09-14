"""Tests for FamilyStore — accounts, consent, recordings, shares, privacy settings."""

import pytest

from src.family_store import FamilyStore, VALID_CONSENT_TYPES


@pytest.fixture
def store(tmp_path):
    return FamilyStore(db_path=str(tmp_path / "family.db"))


# ── Seeding ──────────────────────────────────────────────────────────────────

def test_seed_creates_household_and_account(store):
    hh = store.get_household(1)
    assert hh is not None
    assert hh["name"] == "Demo Family"
    assert len(hh["members"]) == 1
    assert hh["members"][0]["email"] == "guardian@family.local"


def test_seed_creates_consent_records(store):
    consents = store.get_consent(1)
    types = {c["type"] for c in consents}
    assert types == VALID_CONSENT_TYPES
    assert all(c["status"] == "pending" for c in consents)


def test_seed_creates_privacy_settings(store):
    settings = store.get_privacy_settings(1)
    assert settings is not None
    assert settings["retention_days"] == 90
    assert settings["auto_delete_enabled"] == 1
    assert settings["allow_clinician_replay"] == 1


def test_seed_is_idempotent(tmp_path):
    db = str(tmp_path / "family.db")
    FamilyStore(db_path=db)
    FamilyStore(db_path=db)  # second init must not create a second household
    store = FamilyStore(db_path=db)
    hh = store.get_household(1)
    assert len(hh["members"]) == 1


# ── Auth ─────────────────────────────────────────────────────────────────────

def test_login_success(store):
    result = store.login("guardian@family.local", "1234")
    assert result is not None
    assert "token" in result
    assert result["account"]["email"] == "guardian@family.local"
    assert "pin_hash" not in result["account"]
    assert "pin_salt" not in result["account"]


def test_login_wrong_pin(store):
    assert store.login("guardian@family.local", "0000") is None


def test_login_unknown_email(store):
    assert store.login("nobody@example.com", "1234") is None


def test_resolve_session_valid(store):
    result = store.login("guardian@family.local", "1234")
    token = result["token"]
    account = store.resolve_session(token)
    assert account is not None
    assert account["email"] == "guardian@family.local"


def test_resolve_session_invalid_token(store):
    assert store.resolve_session("badtoken") is None


def test_logout_invalidates_token(store):
    result = store.login("guardian@family.local", "1234")
    token = result["token"]
    assert store.logout(token) is True
    assert store.resolve_session(token) is None


def test_logout_unknown_token(store):
    assert store.logout("nonexistent") is False


# ── Consent ──────────────────────────────────────────────────────────────────

def test_update_consent_grant(store):
    updated = store.update_consent(1, "primary_consent", "active", 1)
    assert updated is not None
    assert updated["status"] == "active"
    assert updated["granted_at"] is not None


def test_update_consent_withdraw(store):
    store.update_consent(1, "primary_consent", "active", 1)
    updated = store.update_consent(1, "primary_consent", "withdrawn", 1)
    assert updated["status"] == "withdrawn"


def test_update_consent_invalid_type(store):
    assert store.update_consent(1, "unknown_type", "active", 1) is None


def test_update_consent_invalid_status(store):
    assert store.update_consent(1, "primary_consent", "approved", 1) is None


# ── Recordings ───────────────────────────────────────────────────────────────

def test_create_and_get_recording(store):
    rec_id = store.create_recording(1, "Morning session", "2026-09-14T08:00:00+00:00")
    assert isinstance(rec_id, int)
    rec = store.get_recording(rec_id, 1)
    assert rec is not None
    assert rec["label"] == "Morning session"
    assert rec["event_ids"] == []


def test_stop_recording_sets_duration(store):
    rec_id = store.create_recording(1, None, "2026-09-14T08:00:00+00:00")
    ok = store.stop_recording(rec_id, "2026-09-14T08:05:00+00:00", [])
    assert ok is True
    rec = store.get_recording(rec_id, 1)
    assert rec["duration_s"] == pytest.approx(300.0)
    assert rec["event_ids"] == []


def test_stop_recording_links_event_ids(store):
    rec_id = store.create_recording(1, None, "2026-09-14T08:00:00+00:00")
    store.stop_recording(rec_id, "2026-09-14T08:05:00+00:00", [7, 8, 9])
    rec = store.get_recording(rec_id, 1)
    assert rec["event_ids"] == [7, 8, 9]


def test_stop_recording_not_found(store):
    assert store.stop_recording(9999, "2026-09-14T08:05:00+00:00", []) is False


def test_get_recordings_excludes_deleted(store):
    rec_id = store.create_recording(1, "to delete", "2026-09-14T08:00:00+00:00")
    store.delete_recording(rec_id, 1)
    recs = store.get_recordings(1)
    assert all(r["id"] != rec_id for r in recs)


def test_delete_recording_wrong_household(store):
    rec_id = store.create_recording(1, "mine", "2026-09-14T08:00:00+00:00")
    assert store.delete_recording(rec_id, 999) is False


def test_get_recording_wrong_household(store):
    rec_id = store.create_recording(1, "mine", "2026-09-14T08:00:00+00:00")
    assert store.get_recording(rec_id, 999) is None


# ── Clinician Shares ─────────────────────────────────────────────────────────

def test_create_and_get_share(store):
    share = store.create_share(
        household_id=1,
        recording_id=None,
        clinician_name="Dr. Smith",
        clinician_email="dr.smith@clinic.org",
        account_id=1,
    )
    assert share["clinician_name"] == "Dr. Smith"
    assert share["share_token"] is not None

    shares = store.get_shares(1)
    assert len(shares) == 1
    assert shares[0]["id"] == share["id"]


def test_get_share_by_token(store):
    share = store.create_share(1, None, "Dr. Jones", "j@clinic.org", 1)
    fetched = store.get_share_by_token(share["share_token"])
    assert fetched is not None
    assert fetched["clinician_email"] == "j@clinic.org"


def test_get_share_by_token_invalid(store):
    assert store.get_share_by_token("bogustoken") is None


def test_revoke_share(store):
    share = store.create_share(1, None, "Dr. A", "a@clinic.org", 1)
    assert store.revoke_share(share["id"], 1) is True
    assert store.get_share_by_token(share["share_token"]) is None
    assert store.get_shares(1) == []


def test_revoke_share_wrong_household(store):
    share = store.create_share(1, None, "Dr. B", "b@clinic.org", 1)
    assert store.revoke_share(share["id"], 999) is False


def test_share_with_expiry(store):
    share = store.create_share(1, None, "Dr. C", "c@clinic.org", 1, expires_days=30)
    assert share["expires_at"] is not None
    fetched = store.get_share_by_token(share["share_token"])
    assert fetched is not None  # not yet expired


# ── Privacy Settings ─────────────────────────────────────────────────────────

def test_update_privacy_settings(store):
    updated = store.update_privacy_settings(1, retention_days=180, auto_delete_enabled=False)
    assert updated["retention_days"] == 180
    assert updated["auto_delete_enabled"] == 0
    assert updated["allow_clinician_replay"] == 1  # unchanged


def test_update_privacy_settings_unknown_household(store):
    assert store.update_privacy_settings(9999, retention_days=30) is None


# ── Retention policy ─────────────────────────────────────────────────────────

def test_apply_retention_policy_removes_old_deleted(store):
    rec_id = store.create_recording(1, "old", "2024-01-01T00:00:00+00:00")
    # Manually soft-delete with an old timestamp
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE recordings SET deleted_at = '2024-01-02T00:00:00+00:00' WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()

    count = store.apply_retention_policy(1)
    assert count == 1
    assert store.get_recording(rec_id, 1) is None


def test_apply_retention_policy_skips_when_disabled(store):
    store.update_privacy_settings(1, auto_delete_enabled=False)
    rec_id = store.create_recording(1, "old", "2024-01-01T00:00:00+00:00")
    import sqlite3
    conn = sqlite3.connect(store.db_path)
    conn.execute(
        "UPDATE recordings SET deleted_at = '2024-01-02T00:00:00+00:00' WHERE id = ?",
        (rec_id,),
    )
    conn.commit()
    conn.close()

    count = store.apply_retention_policy(1)
    assert count == 0

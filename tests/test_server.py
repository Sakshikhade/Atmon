"""Tests for Dashboard HTTP server and REST API."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from src.event_storage import EventStorage
from src.family_store import FamilyStore
from src.server import DashboardRequestHandler


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def test_server(tmp_path):
    db_file = str(tmp_path / "outbox.db")
    storage = EventStorage(db_path=db_file)

    # Seed sample episode
    storage.add_event(
        type="stimming",
        subtype="rhythmic_stimming",
        action="repetitive_wrist_motion",
        started_at="2026-09-02T14:30:00Z",
        ended_at="2026-09-02T14:30:03Z",
        duration_s=3.0,
        source="ingest",
        snapshot={
            "fps": 10.0,
            "frames": [{"t": 0.0, "points": [{"i": 0, "x": 0.5, "y": 0.5, "z": 0.0}]}],
        },
    )

    DashboardRequestHandler.server_storage = storage
    DashboardRequestHandler.family_store = None
    # Bind to port 0 for random free port
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, storage

    server.shutdown()
    server.server_close()


@pytest.fixture
def family_server(tmp_path):
    """Server with both EventStorage and FamilyStore wired up."""
    db_file = str(tmp_path / "outbox.db")
    family_db = str(tmp_path / "family.db")
    storage = EventStorage(db_path=db_file)
    family_store = FamilyStore(db_path=family_db)

    DashboardRequestHandler.server_storage = storage
    DashboardRequestHandler.family_store = family_store

    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, storage, family_store

    server.shutdown()
    server.server_close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _request(method, url, body=None, token=None):
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _login(base_url):
    status, data = _request("POST", f"{base_url}/api/family/auth/login",
                             {"email": "guardian@family.local", "pin": "1234"})
    assert status == 200
    return data["token"]


# ── Dashboard tests ────────────────────────────────────────────────────────────

def test_server_root_serves_html(test_server):
    base_url, _ = test_server
    with urlopen(f"{base_url}/") as res:
        assert res.status == 200
        assert "text/html" in res.headers.get("Content-Type", "")
        body = res.read().decode("utf-8")
        assert "AAMAS" in body
        assert "skeleton-canvas" in body


def test_server_api_stats(test_server):
    base_url, _ = test_server
    with urlopen(f"{base_url}/api/stats") as res:
        assert res.status == 200
        data = json.loads(res.read().decode("utf-8"))
        assert data["total_episodes"] == 1
        assert data["by_type"]["stimming"] == 1
        assert data["snapshots_count"] == 1


def test_server_api_episodes(test_server):
    base_url, _ = test_server
    with urlopen(f"{base_url}/api/episodes") as res:
        assert res.status == 200
        episodes = json.loads(res.read().decode("utf-8"))
        assert len(episodes) == 1
        assert episodes[0]["type"] == "stimming"
        assert episodes[0]["has_snapshot"] == 1


def test_server_api_single_episode(test_server):
    base_url, storage = test_server
    episodes = storage.get_episodes()
    ep_id = episodes[0]["id"]

    with urlopen(f"{base_url}/api/episodes/{ep_id}") as res:
        assert res.status == 200
        ep = json.loads(res.read().decode("utf-8"))
        assert ep["id"] == ep_id
        assert ep["snapshot"] is not None
        assert len(ep["snapshot"]["frames"]) == 1


def test_server_family_ui(family_server):
    base_url, _, _ = family_server
    with urlopen(f"{base_url}/family") as res:
        assert res.status == 200
        assert "text/html" in res.headers.get("Content-Type", "")
        body = res.read().decode("utf-8")
        assert "Atmos Family" in body


# ── Family auth tests ─────────────────────────────────────────────────────────

def test_family_login_success(family_server):
    base_url, _, _ = family_server
    status, data = _request("POST", f"{base_url}/api/family/auth/login",
                             {"email": "guardian@family.local", "pin": "1234"})
    assert status == 200
    assert "token" in data
    assert data["account"]["email"] == "guardian@family.local"
    assert "pin_hash" not in data["account"]


def test_family_login_wrong_pin(family_server):
    base_url, _, _ = family_server
    status, data = _request("POST", f"{base_url}/api/family/auth/login",
                             {"email": "guardian@family.local", "pin": "9999"})
    assert status == 401


def test_family_auth_me(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, data = _request("GET", f"{base_url}/api/family/auth/me", token=token)
    assert status == 200
    assert data["email"] == "guardian@family.local"


def test_family_auth_me_no_token(family_server):
    base_url, _, _ = family_server
    status, _ = _request("GET", f"{base_url}/api/family/auth/me")
    assert status == 401


def test_family_logout(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, _ = _request("POST", f"{base_url}/api/family/auth/logout", {}, token=token)
    assert status == 200
    # Token no longer valid
    status2, _ = _request("GET", f"{base_url}/api/family/auth/me", token=token)
    assert status2 == 401


# ── Household tests ───────────────────────────────────────────────────────────

def test_family_household(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, data = _request("GET", f"{base_url}/api/family/household", token=token)
    assert status == 200
    assert data["name"] == "Demo Family"
    assert len(data["members"]) == 1


# ── Consent tests ─────────────────────────────────────────────────────────────

def test_family_consent_list(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, data = _request("GET", f"{base_url}/api/family/consent", token=token)
    assert status == 200
    assert isinstance(data, list)
    types = {c["type"] for c in data}
    assert "primary_consent" in types


def test_family_consent_grant_and_withdraw(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, data = _request("POST", f"{base_url}/api/family/consent",
                             {"type": "primary_consent", "status": "active"}, token=token)
    assert status == 200
    assert data["status"] == "active"

    status2, data2 = _request("POST", f"{base_url}/api/family/consent",
                               {"type": "primary_consent", "status": "withdrawn"}, token=token)
    assert status2 == 200
    assert data2["status"] == "withdrawn"


def test_family_consent_invalid_type(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, _ = _request("POST", f"{base_url}/api/family/consent",
                          {"type": "bad_type", "status": "active"}, token=token)
    assert status == 400


# ── Capture flow tests ────────────────────────────────────────────────────────

def test_family_capture_start_stop(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)

    # Start capture
    status, data = _request("POST", f"{base_url}/api/family/capture/start",
                             {"label": "Test session"}, token=token)
    assert status == 201
    rec_id = data["recording_id"]
    assert isinstance(rec_id, int)

    # Stop capture
    status2, data2 = _request("POST", f"{base_url}/api/family/capture/stop",
                               {"recording_id": rec_id, "event_ids": []}, token=token)
    assert status2 == 200
    assert data2["duration_s"] is not None


def test_family_capture_stop_missing_id(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, _ = _request("POST", f"{base_url}/api/family/capture/stop",
                          {"event_ids": []}, token=token)
    assert status == 400


def test_family_capture_auto_links_events(family_server):
    """Events in EventStorage within recording window are auto-linked on stop."""
    base_url, storage, _ = family_server
    token = _login(base_url)

    started = "2026-09-14T08:00:00+00:00"
    ended = "2026-09-14T08:10:00+00:00"
    storage.add_event(
        type="stimming", subtype=None, action="hand_flap",
        started_at="2026-09-14T08:05:00+00:00",
        ended_at="2026-09-14T08:05:02+00:00",
        duration_s=2.0, source="ingest",
    )

    status, data = _request("POST", f"{base_url}/api/family/capture/start",
                             {"started_at": started}, token=token)
    assert status == 201
    rec_id = data["recording_id"]

    status2, data2 = _request("POST", f"{base_url}/api/family/capture/stop",
                               {"recording_id": rec_id, "ended_at": ended, "event_ids": []},
                               token=token)
    assert status2 == 200
    assert len(data2["event_ids"]) >= 1


# ── Recording library tests ───────────────────────────────────────────────────

def test_family_recordings_list(family_server):
    base_url, _, fs = family_server
    token = _login(base_url)
    fs.create_recording(1, "Session A", "2026-09-14T09:00:00+00:00")

    status, data = _request("GET", f"{base_url}/api/family/recordings", token=token)
    assert status == 200
    assert any(r["label"] == "Session A" for r in data)


def test_family_recording_detail_with_events(family_server):
    base_url, storage, fs = family_server
    token = _login(base_url)
    ep_id = storage.add_event(
        type="stimming", subtype=None, action="rocking",
        started_at="2026-09-14T09:00:01+00:00",
        ended_at="2026-09-14T09:00:03+00:00",
        duration_s=2.0, source="ingest",
        snapshot={"fps": 10, "frames": [{"t": 0.0, "points": []}]},
    )
    rec_id = fs.create_recording(1, "Detail test", "2026-09-14T09:00:00+00:00")
    fs.stop_recording(rec_id, "2026-09-14T09:05:00+00:00", [ep_id])

    status, data = _request("GET", f"{base_url}/api/family/recordings/{rec_id}", token=token)
    assert status == 200
    assert data["label"] == "Detail test"
    assert len(data["events"]) == 1
    assert data["events"][0]["id"] == ep_id


def test_family_recording_delete(family_server):
    base_url, _, fs = family_server
    token = _login(base_url)
    rec_id = fs.create_recording(1, "To delete", "2026-09-14T09:00:00+00:00")

    status, _ = _request("DELETE", f"{base_url}/api/family/recordings/{rec_id}", token=token)
    assert status == 200

    status2, _ = _request("GET", f"{base_url}/api/family/recordings/{rec_id}", token=token)
    assert status2 == 404


# ── Share tests ───────────────────────────────────────────────────────────────

def test_family_share_create_and_list(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)

    status, data = _request("POST", f"{base_url}/api/family/shares",
                             {"clinician_name": "Dr. Smith",
                              "clinician_email": "dr.smith@clinic.org"}, token=token)
    assert status == 201
    share_id = data["id"]

    status2, shares = _request("GET", f"{base_url}/api/family/shares", token=token)
    assert status2 == 200
    assert any(s["id"] == share_id for s in shares)


def test_family_share_revoke(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)

    _, data = _request("POST", f"{base_url}/api/family/shares",
                        {"clinician_name": "Dr. A", "clinician_email": "a@clinic.org"}, token=token)
    share_id = data["id"]

    status, _ = _request("DELETE", f"{base_url}/api/family/shares/{share_id}", token=token)
    assert status == 200

    _, shares = _request("GET", f"{base_url}/api/family/shares", token=token)
    assert all(s["id"] != share_id for s in shares)


def test_family_share_missing_fields(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, _ = _request("POST", f"{base_url}/api/family/shares",
                          {"clinician_name": "Dr. B"}, token=token)
    assert status == 400


def test_family_clinician_review(family_server):
    base_url, _, fs = family_server
    token = _login(base_url)
    share = fs.create_share(1, None, "Dr. C", "c@clinic.org", 1)
    share_token = share["share_token"]

    status, data = _request("GET", f"{base_url}/api/family/clinician/review/{share_token}")
    assert status == 200
    assert "share" in data


def test_family_clinician_review_invalid_token(family_server):
    base_url, _, _ = family_server
    status, _ = _request("GET", f"{base_url}/api/family/clinician/review/badtoken")
    assert status == 404


# ── Privacy settings tests ────────────────────────────────────────────────────

def test_family_privacy_get(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, data = _request("GET", f"{base_url}/api/family/privacy", token=token)
    assert status == 200
    assert "retention_days" in data


def test_family_privacy_update(family_server):
    base_url, _, _ = family_server
    token = _login(base_url)
    status, data = _request("PUT", f"{base_url}/api/family/privacy",
                             {"retention_days": 180, "auto_delete_enabled": False,
                              "allow_clinician_replay": True}, token=token)
    assert status == 200
    assert data["retention_days"] == 180
    assert data["auto_delete_enabled"] == 0


# ── Feature flags test ────────────────────────────────────────────────────────

def test_family_features(family_server):
    base_url, _, _ = family_server
    status, data = _request("GET", f"{base_url}/api/family/features")
    assert status == 200
    assert "home_camera_phase2" in data

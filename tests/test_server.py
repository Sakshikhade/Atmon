"""Tests for Dashboard HTTP server and REST API."""

import json
import threading
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

import pytest

from src.event_storage import EventStorage
from src.server import DashboardRequestHandler


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
    # Bind to port 0 for random free port
    server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardRequestHandler)
    port = server.server_port
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()

    base_url = f"http://127.0.0.1:{port}"
    yield base_url, storage

    server.shutdown()
    server.server_close()


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

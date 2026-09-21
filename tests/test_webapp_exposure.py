"""Binding a public interface must be a deliberate act, not an env var away.

The app has no auth. /api/clips/delete_all removes every saved clip and
/api/classes rewrites config.yaml, and the README points at EC2 -- so
HOST=0.0.0.0 would have exposed both, plus the camera, to the network.
"""

import importlib

import pytest
from fastapi.testclient import TestClient

import webapp.server as server


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1"])
def test_loopback_needs_no_token(host):
    """The default local workflow is untouched."""
    server.check_exposure(host, "")


@pytest.mark.parametrize("value", [None, "", "  "])
def test_blank_host_env_resolves_to_loopback(value):
    """`HOST=` must not sneak past the gate: uvicorn binds "" to 0.0.0.0."""
    assert server.resolve_host(value) == "127.0.0.1"
    server.check_exposure(server.resolve_host(value), "")


def test_empty_host_is_not_treated_as_loopback():
    assert not server.is_loopback("")
    with pytest.raises(SystemExit):
        server.check_exposure("", "")


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "example.com"])
def test_public_bind_without_token_refuses(host):
    with pytest.raises(SystemExit) as excinfo:
        server.check_exposure(host, "")
    message = str(excinfo.value)
    assert "APP_TOKEN" in message
    # The refusal must say what is at stake, not just "denied".
    assert "delete_all" in message and "config.yaml" in message


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5"])
def test_public_bind_with_token_is_allowed(host):
    server.check_exposure(host, "s3cret")


# -- the middleware ----------------------------------------------------------

@pytest.fixture()
def tokened(monkeypatch):
    """Reload the module with APP_TOKEN set, so the middleware sees it."""
    monkeypatch.setenv("APP_TOKEN", "test-token")
    mod = importlib.reload(server)
    try:
        yield mod
    finally:
        monkeypatch.delenv("APP_TOKEN", raising=False)
        importlib.reload(server)


def test_reads_stay_open(tokened):
    """An unauthenticated browser must still be able to render the page."""
    client = TestClient(tokened.app)
    assert client.get("/api/state").status_code == 200


# NEVER exercise a destructive route here. These tests run against the real
# config.yaml, so a POST to /api/clips/delete_all really does walk data/clips
# and unlink every file -- it destroyed 8 saved sessions once already. The gate
# is a middleware and does not care which path it guards, so assert on a route
# that cannot damage anything: /api/record/stop 409s when nothing is recording,
# which is a response from the handler, i.e. proof the token got through.
SAFE_MUTATING_ROUTE = "/api/record/stop"


def test_mutating_request_without_token_is_401(tokened):
    client = TestClient(tokened.app)
    assert client.post(SAFE_MUTATING_ROUTE).status_code == 401
    # Also safe when idle: 409s on "no prototype bank" / already-busy checks.
    assert client.post("/api/live/stop").status_code == 401


def test_mutating_request_with_token_passes_the_gate(tokened):
    client = TestClient(tokened.app)
    r = client.post(SAFE_MUTATING_ROUTE,
                    headers={"Authorization": "Bearer test-token"})
    assert r.status_code != 401, "a valid token must reach the handler"
    assert r.status_code == 409, "and be refused by the handler, not the gate"


def test_wrong_token_is_401(tokened):
    client = TestClient(tokened.app)
    r = client.post(SAFE_MUTATING_ROUTE,
                    headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_frame_ingest_is_exempt(tokened):
    """~8 POSTs/sec from the page; a token there would live in client-side JS.

    It must fail for its own reason (409, no active ingest), not on auth.
    """
    client = TestClient(tokened.app)
    r = client.post("/api/frame", content=b"not-a-jpeg")
    assert r.status_code != 401


def test_no_token_configured_leaves_everything_open():
    """Unset APP_TOKEN reproduces today's behaviour exactly."""
    assert server.APP_TOKEN == ""
    client = TestClient(server.app)
    assert client.post(SAFE_MUTATING_ROUTE).status_code != 401

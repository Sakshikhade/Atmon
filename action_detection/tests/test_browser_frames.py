"""Browser webcam ingest for the web demo (headless hosts have no OpenCV cameras)."""

import io
import time

import numpy as np
import pytest

import webapp.server as server_mod  # noqa: E402

def _jpeg_bytes(rgb):
    import cv2

    ok, buf = cv2.imencode(".jpg", cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR))
    assert ok
    return buf.tobytes()

def test_browser_frame_stream_push_read():
    stream = server_mod.BrowserFrameStream(warmup_sec=2.0).start()
    try:
        rgb = np.zeros((48, 64, 3), dtype=np.uint8)
        rgb[:, :] = (40, 80, 120)
        assert stream.push_jpeg(_jpeg_bytes(rgb))
        got = stream.read()
        assert got is not None
        assert got.shape == (48, 64, 3)
        assert stream.read() is None  # consumed
    finally:
        stream.release()

def test_api_frame_requires_active_ingest(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setattr(server_mod, "CONFIG_PATH", str(tmp_path / "config.yaml"))
    client = TestClient(server_mod.app)
    res = client.post("/api/frame", content=b"not-a-jpeg", headers={"Content-Type": "image/jpeg"})
    assert res.status_code == 409

def test_api_frame_accepts_jpeg_while_ingesting():
    from fastapi.testclient import TestClient

    stream = server_mod.BrowserFrameStream(warmup_sec=5.0).start()
    server_mod._bind_ingest(stream)
    with server_mod.STATE.lock:
        server_mod.STATE.mode = "detecting"
    try:
        client = TestClient(server_mod.app)
        rgb = np.full((32, 32, 3), 90, dtype=np.uint8)
        res = client.post("/api/frame", content=_jpeg_bytes(rgb), headers={"Content-Type": "image/jpeg"})
        assert res.status_code == 200
        assert stream.captured >= 1
        frame = stream.read()
        assert frame is not None
    finally:
        stream.release()
        server_mod._clear_ingest(stream)
        with server_mod.STATE.lock:
            server_mod.STATE.mode = "idle"

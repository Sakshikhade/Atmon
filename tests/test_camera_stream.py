"""Unit tests for ThreadedCameraStream disconnect/reconnect handling.

All tests mock cv2.VideoCapture to avoid requiring a physical camera.
The three scenarios:
  1. Transient failure on a live camera — recovers via reconnect.
  2. Sustained failure on a live camera — gives up after max attempts.
  3. End-of-file on a video source — clean stop, no reconnect attempt.
"""

import time
from unittest.mock import MagicMock, patch

import numpy as np

from src.camera_stream import ThreadedCameraStream


def _make_frame():
    """Return a small dummy BGR frame (10x10 pixels)."""
    return np.zeros((10, 10, 3), dtype=np.uint8)


# ---------------------------------------------------------------------------
# Scenario 1: Live camera — transient failure then recovery
# ---------------------------------------------------------------------------
def test_live_camera_recovers_from_transient_failure():
    """After a brief read failure, the stream reopens the capture and resumes."""
    good_frame = _make_frame()

    # First stream.read() in __init__ succeeds, then the update loop will read from
    # the mocked capture. We'll control the sequence via a side-effect list.
    read_sequence = [
        (True, good_frame),  # __init__ read
        (True, good_frame),  # first loop read — normal
        (False, None),  # second loop read — transient failure
        # _attempt_reconnect re-creates cv2.VideoCapture and calls read():
        (True, good_frame),  # reconnect attempt 1 — succeeds
        (True, good_frame),  # normal frame after reconnect
        (True, good_frame),  # one more to confirm stable
    ]
    call_idx = {"i": 0}

    def mock_read():
        idx = call_idx["i"]
        call_idx["i"] += 1
        if idx < len(read_sequence):
            return read_sequence[idx]
        return (True, good_frame)

    mock_cap_instance = MagicMock()
    mock_cap_instance.read = mock_read

    with patch("src.camera_stream.cv2.VideoCapture", return_value=mock_cap_instance):
        stream = ThreadedCameraStream(src=0, max_reconnect_attempts=3, reconnect_delay=0.01)
        assert stream.is_live is True
        stream.start()
        time.sleep(0.3)  # give the thread time to hit the failure + reconnect
        # While alive (before release), a transient failure must NOT have stopped it.
        recovered_alive = not stream.stopped
        stream.release()

    # The stream recovered from the transient failure rather than dying on it.
    assert recovered_alive, "stream stopped on a transient failure instead of reconnecting"
    # The failure (idx 2) + a successful reconnect read (idx 3) must both have been consumed.
    assert call_idx["i"] > 3, "reconnect path was not exercised"


# ---------------------------------------------------------------------------
# Scenario 2: Live camera — sustained failure, gives up
# ---------------------------------------------------------------------------
def test_live_camera_gives_up_after_max_attempts():
    """After max_reconnect_attempts all fail, the stream stops permanently."""
    good_frame = _make_frame()

    read_sequence = [
        (True, good_frame),  # __init__ read
        (False, None),  # first loop read — failure
        # All reconnect attempts fail:
        (False, None),
        (False, None),
        (False, None),
    ]
    call_idx = {"i": 0}

    def mock_read():
        idx = call_idx["i"]
        call_idx["i"] += 1
        if idx < len(read_sequence):
            return read_sequence[idx]
        return (False, None)

    mock_cap_instance = MagicMock()
    mock_cap_instance.read = mock_read

    with patch("src.camera_stream.cv2.VideoCapture", return_value=mock_cap_instance):
        stream = ThreadedCameraStream(src=0, max_reconnect_attempts=3, reconnect_delay=0.01)
        stream.start()
        time.sleep(0.5)  # enough time for all attempts + backoffs
        ret, frame = stream.read()
        stream.release()

    # Stream should be permanently stopped.
    assert stream.stopped is True
    assert ret is False
    assert frame is None


# ---------------------------------------------------------------------------
# Scenario 3: Video file — end-of-stream, no reconnect
# ---------------------------------------------------------------------------
def test_video_file_stops_cleanly_at_eof():
    """A file source reaching EOF stops cleanly without attempting to reconnect."""
    good_frame = _make_frame()

    read_sequence = [
        (True, good_frame),  # __init__ read
        (True, good_frame),  # first loop read — normal
        (False, None),  # second loop read — EOF
    ]
    call_idx = {"i": 0}

    def mock_read():
        idx = call_idx["i"]
        call_idx["i"] += 1
        if idx < len(read_sequence):
            return read_sequence[idx]
        return (False, None)

    mock_cap_instance = MagicMock()
    mock_cap_instance.read = mock_read

    with patch("src.camera_stream.cv2.VideoCapture", return_value=mock_cap_instance):
        stream = ThreadedCameraStream(
            src="samples/sample-2.mp4", max_reconnect_attempts=3, reconnect_delay=0.01
        )
        assert stream.is_live is False
        stream.start()
        time.sleep(0.2)
        ret, frame = stream.read()
        stream.release()

    # Stream should be stopped (EOF), and no reconnect was attempted.
    assert stream.stopped is True
    assert ret is False
    # The mock_read was called at most 3 times (init + normal + EOF); no extra reconnect calls.
    assert call_idx["i"] == 3

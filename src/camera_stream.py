import threading
import time

import cv2

from src.logging_config import get_logger

logger = get_logger(__name__)


class ThreadedCameraStream:
    """
    ThreadedCameraStream decouples webcam frame ingestion from the main processing pipeline.
    It continuously reads frames from cv2.VideoCapture in a background thread, caching only the
    most recent frame in RAM with thread-safe locks. In line with Privacy-by-Design,
    cached frames are immediately cleared from memory once read by the main loop.

    Disconnect/reconnect handling:
    - For a **live camera** (integer source), a failed read is treated as a transient
      disconnect: the stream attempts to reopen the capture up to ``max_reconnect_attempts``
      times with a short backoff before giving up permanently.
    - For a **video file** (string source path), a failed read is end-of-stream and stops
      the thread cleanly (no reconnect — the file simply ended).
    """

    def __init__(self, src=0, max_reconnect_attempts=5, reconnect_delay=1.0):
        self.src = src
        # Live camera sources are integer device indices; file sources are string paths.
        self.is_live = isinstance(src, int)
        self.max_reconnect_attempts = max_reconnect_attempts
        self.reconnect_delay = reconnect_delay

        self.stream = cv2.VideoCapture(src)
        self.grabbed, self.frame = self.stream.read()
        self.read_lock = threading.Lock()
        self.stopped = False
        self.started = False
        self.reconnecting = False
        self.thread = None

    def start(self):
        if self.started:
            return self
        self.started = True
        self.stopped = False
        self.thread = threading.Thread(target=self._update, name="CameraIngestionThread")
        self.thread.daemon = True
        self.thread.start()
        return self

    def _attempt_reconnect(self):
        """Try to reopen a live camera source. Returns True if a frame was recovered."""
        for attempt in range(1, self.max_reconnect_attempts + 1):
            if self.stopped:
                return False
            logger.warning(
                "Camera read failed; reconnect attempt %d/%d for source %s",
                attempt,
                self.max_reconnect_attempts,
                self.src,
            )
            try:
                self.stream.release()
            except Exception:
                pass
            time.sleep(self.reconnect_delay)
            self.stream = cv2.VideoCapture(self.src)
            grabbed, frame = self.stream.read()
            if grabbed:
                logger.info("Camera reconnected on attempt %d for source %s", attempt, self.src)
                with self.read_lock:
                    self.grabbed = grabbed
                    self.frame = frame
                return True
        logger.error(
            "Camera reconnect failed after %d attempts for source %s; stopping stream",
            self.max_reconnect_attempts,
            self.src,
        )
        return False

    def _update(self):
        while not self.stopped:
            grabbed, frame = self.stream.read()
            if not grabbed:
                if not self.is_live:
                    # Video file: a failed read means end-of-stream. Stop cleanly.
                    logger.info("End of video source reached: %s", self.src)
                    self.stop()
                    break
                # Live camera: treat as a transient disconnect and try to reconnect.
                self.reconnecting = True
                recovered = self._attempt_reconnect()
                self.reconnecting = False
                if not recovered:
                    self.stop()
                    break
                # Successful reconnect already cached a fresh frame; continue.
                continue
            with self.read_lock:
                self.grabbed = grabbed
                self.frame = frame
            # Yield CPU briefly between frames to prevent thread starvation
            time.sleep(0.005)

    def read(self):
        """
        Reads the latest frame from the cache.
        Returns (success_status, frame).

        - ``(False, None)``  — the stream has permanently stopped (file EOF or a live
          camera that could not be recovered).
        - ``(True, None)``   — no new frame available yet, or a transient reconnect is in
          progress; the caller should keep polling rather than exit.
        - ``(True, frame)``  — a fresh frame; the cached copy is immediately purged to
          maintain strict Privacy-by-Design.
        """
        with self.read_lock:
            if self.stopped:
                return False, None
            if self.frame is None:
                # No new frame available yet (or reconnecting) — signal "keep waiting".
                return True, None
            frame = self.frame
            self.frame = None
            return True, frame

    def stop(self):
        self.stopped = True

    def release(self):
        self.stop()
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        self.stream.release()

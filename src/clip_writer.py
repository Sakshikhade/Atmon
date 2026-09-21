"""Write a video clip for each detected event.

This is the one part of the system that puts recorded video on disk. Everything
else works on embeddings and timestamps, so treat what lands in `clips.dir` as
sensitive: it is footage of the monitored person, retained indefinitely until
either the size budget evicts it or someone deletes it.

Two sources, one output shape:

  offline  extract_clips_from_video() -- one sequential pass over the source
           file, writing every requested span at NATIVE fps and resolution.
  live     ClipWriter.write_frames()  -- from the retention buffer, at
           working_fps, because those are the only frames that were kept.

Live clips are therefore choppier than offline ones. That is not a bug: the live
path never had the frames in between.
"""

import os
import time

BYTES_PER_GB = 1024 ** 3


# H.264 first. mp4v is MPEG-4 Part 2, which OpenCV writes happily and modern
# browsers / QuickTime cannot play (blank or green frame). OpenCV's "avc1" on
# headless Linux often fails open (no V4L2 encoder), so live clips silently
# fell back to mp4v and the gallery looked broken. Prefer PyAV libx264.
CODECS = ("avc1", "mp4v")


def open_writer(path, fps, size, codecs=CODECS):
    """VideoWriter using the first codec this build can actually open.

    Returns (writer, codec). Raises if none work.
    Prefer write_frames() for new code -- it uses browser-playable H.264.
    """
    import cv2

    for codec in codecs:
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), float(fps), size)
        if writer.isOpened():
            return writer, codec
        writer.release()
    raise RuntimeError(
        "no usable video codec for %s -- tried %s" % (path, ", ".join(codecs)))


def _even(n):
    n = int(n)
    return n if n % 2 == 0 else n + 1


class H264Writer:
    """Streaming browser-playable H.264/yuv420p writer (PyAV, libx264).

    Frames are encoded and muxed AS THEY ARRIVE. That is the whole point:
    holding a span in memory to encode it at the end costs width*height*3 per
    frame, so one 60s clip of 1080p source video is ~11 GB, and a pass that
    collects several spans at once multiplies that.

    Raises from the constructor if PyAV/libx264 is unavailable, so a caller can
    fall back to open_writer() before it has consumed any frames.
    """

    def __init__(self, path, fps, size, is_rgb=True):
        import av

        self._av = av
        self.path = path
        self.is_rgb = bool(is_rgb)
        self.src_w, self.src_h = int(size[0]), int(size[1])
        # libx264 + yuv420p needs even dimensions; an odd source is padded.
        self.out_w, self.out_h = _even(self.src_w), _even(self.src_h)

        self.container = av.open(path, mode="w")
        try:
            rate = max(1, int(round(float(fps))) or 8)
            stream = self.container.add_stream("libx264", rate=rate)
            stream.width = self.out_w
            stream.height = self.out_h
            stream.pix_fmt = "yuv420p"
            stream.options = {"crf": "23", "preset": "veryfast"}
            self.stream = stream
        except Exception:
            self.container.close()
            raise

    def write(self, frame):
        import numpy as np

        if not self.is_rgb:
            # BGR (OpenCV) -> RGB for VideoFrame
            import cv2

            frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        if frame.shape[1] != self.out_w or frame.shape[0] != self.out_h:
            padded = np.zeros((self.out_h, self.out_w, 3), dtype=np.uint8)
            padded[: frame.shape[0], : frame.shape[1]] = frame
            frame = padded
        video_frame = self._av.VideoFrame.from_ndarray(frame, format="rgb24")
        for packet in self.stream.encode(video_frame):
            self.container.mux(packet)

    def close(self):
        try:
            for packet in self.stream.encode():
                self.container.mux(packet)
        finally:
            self.container.close()


def write_frames_h264(path, frames, fps, is_rgb=True):
    """Write frames as browser-playable H.264/yuv420p via PyAV (libx264).

    frames: iterable of HxWx3 uint8 arrays (RGB if is_rgb else BGR).
    Returns path. Raises if libx264 is unavailable.
    """
    iterator = iter(frames)
    try:
        first = next(iterator)
    except StopIteration:
        raise ValueError("no frames")

    height, width = first.shape[:2]
    writer = H264Writer(path, fps, (width, height), is_rgb=is_rgb)
    try:
        writer.write(first)
        for frame in iterator:
            writer.write(frame)
    finally:
        writer.close()
    return path


class ClipStore:
    """Path layout and the size budget for written clips."""

    def __init__(self, root, max_total_gb=5.0, max_clip_sec=60.0, project_root=None):
        self.root = root
        # Paths in the event log are relative to the PROJECT root, so a consumer
        # reading the log from where the scripts run can resolve them. Relative
        # to the clip directory's parent -- the previous behaviour -- produced
        # "clips/<id>/<event>.mp4" for a file actually at "data/clips/...".
        self.project_root = project_root or os.path.dirname(os.path.abspath(root))
        self.max_total_bytes = int(float(max_total_gb) * BYTES_PER_GB)
        self.max_clip_sec = float(max_clip_sec)

    def path_for(self, source_id, event_id):
        directory = os.path.join(self.root, source_id)
        os.makedirs(directory, exist_ok=True)
        return os.path.join(directory, "%s.mp4" % event_id)

    def relative(self, path):
        """Store a path relative to the project root when possible."""
        try:
            return os.path.relpath(path, self.project_root)
        except ValueError:
            return path

    def _all_clips(self):
        found = []
        for directory, _, files in os.walk(self.root):
            for name in files:
                if name.endswith(".mp4"):
                    full = os.path.join(directory, name)
                    try:
                        stat = os.stat(full)
                    except OSError:
                        continue
                    found.append((stat.st_mtime, stat.st_size, full))
        return found

    def total_bytes(self):
        return sum(size for _, size, _ in self._all_clips())

    def enforce_budget(self):
        """Delete oldest clips until the directory is under budget.

        Returns (n_deleted, bytes_freed). The event log keeps pointing at a
        deleted clip -- rows are append-only and are never rewritten -- so a
        clip_path is a record of where the clip WAS written, and readers must
        check the file exists before opening it.
        """
        clips = sorted(self._all_clips())          # oldest mtime first
        total = sum(size for _, size, _ in clips)
        deleted = freed = 0
        for _, size, path in clips:
            if total <= self.max_total_bytes:
                break
            try:
                os.remove(path)
            except OSError:
                continue
            total -= size
            freed += size
            deleted += 1
        return deleted, freed


def write_frames(path, frames, fps):
    """Write RGB uint8 frames to an mp4. Returns the path, or None if empty.

    Uses PyAV H.264 when available so the web gallery can play clips. Falls
    back to OpenCV only if that fails (may produce mp4v that browsers reject).
    """
    if not len(frames):
        return None
    try:
        return write_frames_h264(path, frames, fps, is_rgb=True)
    except Exception:
        import cv2

        height, width = frames[0].shape[:2]
        writer, _ = open_writer(path, fps, (width, height))
        try:
            for frame in frames:
                writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
        finally:
            writer.release()
        return path


def extract_clips_from_video(video_path, requests, store, source_id, pre_roll=2.0, post_roll=2.0):
    """Write one clip per requested span in a single pass over the source.

    requests: [{"event_id", "start", "end"}, ...] in source-relative seconds.
    Returns {event_id: written_path}. Spans are padded by pre/post roll, clamped
    to the source, and truncated at store.max_clip_sec.

    One pass, not one per detection: re-opening and seeking a long video for
    every event is the obvious implementation and is far slower.
    """
    import cv2

    if not requests:
        return {}

    capture = cv2.VideoCapture(video_path)
    if not capture.isOpened():
        raise RuntimeError("could not open %s for clip extraction" % video_path)

    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        capture.release()
        raise RuntimeError("could not determine fps of %s" % video_path)

    spans = []
    for request in requests:
        start = max(0.0, float(request["start"]) - pre_roll)
        end = float(request["end"]) + post_roll
        end = min(end, start + store.max_clip_sec)
        spans.append({"event_id": request["event_id"], "start": start, "end": end, "writer": None})

    written = {}

    def _close(span):
        writer = span["writer"]
        if writer is None:
            return
        span["writer"] = None
        if isinstance(writer, H264Writer):
            writer.close()
        else:
            writer.release()

    try:
        index = 0
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            t = index / fps
            index += 1
            for span in spans:
                if not (span["start"] <= t <= span["end"]):
                    # Past the end: flush this span now rather than holding an
                    # encoder open for the rest of the pass.
                    if t > span["end"]:
                        _close(span)
                    continue
                if span["writer"] is None:
                    path = store.path_for(source_id, span["event_id"])
                    size = (frame.shape[1], frame.shape[0])
                    # H.264 first: OpenCV's mp4v fallback is unplayable in
                    # browsers. Frames stream into the encoder, never a list --
                    # buffering a whole span is gigabytes on HD source.
                    try:
                        span["writer"] = H264Writer(path, fps, size, is_rgb=False)
                    except Exception:
                        span["writer"], _ = open_writer(path, fps, size)
                    written[span["event_id"]] = path
                span["writer"].write(frame)
    finally:
        try:
            for span in spans:
                _close(span)
        finally:
            capture.release()

    return written


class FrameRetentionBuffer:
    """Rolling buffer of recent frames, JPEG-compressed, for the live path.

    A live detection is only recognised seconds after it began, so the frames
    that make up its opening are already in the past by the time anything knows
    to keep them. This holds a short trailing window so they are still there.

    Retention is the minimum that makes clips possible:
      - normally only `base_horizon_sec` (detection lag + pre-roll) is kept
      - while an event is open, nothing newer than its start is evicted, up to
        a hard ceiling so one stuck detection cannot consume memory without end

    Frames are JPEG-encoded on arrival: raw 640x480 RGB is ~920 KB per frame, so
    a 15-second window would be 110 MB. Compressed it is a few MB.
    """

    def __init__(self, base_horizon_sec, hard_ceiling_sec, jpeg_quality=80):
        self.base_horizon_sec = float(base_horizon_sec)
        self.hard_ceiling_sec = float(hard_ceiling_sec)
        self.jpeg_quality = int(jpeg_quality)
        self.frames = []          # [(capture_sec, jpeg_bytes)], oldest first
        self.floor_sec = None     # earliest time an open event still needs
        self._cv2 = None

    @property
    def cv2(self):
        if self._cv2 is None:
            import cv2

            self._cv2 = cv2
        return self._cv2

    def append(self, capture_sec, frame):
        ok, encoded = self.cv2.imencode(
            ".jpg",
            self.cv2.cvtColor(frame, self.cv2.COLOR_RGB2BGR),
            [int(self.cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality],
        )
        if ok:
            self.frames.append((capture_sec, encoded.tobytes()))
        self._evict(capture_sec)

    def set_floor(self, floor_sec):
        """Retain everything from floor_sec onward; None returns to the base window."""
        self.floor_sec = None if floor_sec is None else float(floor_sec)

    def _evict(self, now_sec):
        cutoff = now_sec - self.base_horizon_sec
        if self.floor_sec is not None:
            cutoff = min(cutoff, self.floor_sec)
        # Never retain more than the ceiling, whatever the floor asks for.
        cutoff = max(cutoff, now_sec - self.hard_ceiling_sec)
        if not self.frames or self.frames[0][0] >= cutoff:
            return
        keep = 0
        for i, (t, _) in enumerate(self.frames):
            if t >= cutoff:
                keep = i
                break
        else:
            keep = len(self.frames)
        del self.frames[:keep]

    def slice(self, start_sec, end_sec):
        """Decoded RGB frames whose capture time falls in [start, end]."""
        import numpy as np

        out = []
        for t, blob in self.frames:
            if start_sec <= t <= end_sec:
                decoded = self.cv2.imdecode(np.frombuffer(blob, dtype=np.uint8), self.cv2.IMREAD_COLOR)
                if decoded is not None:
                    out.append(self.cv2.cvtColor(decoded, self.cv2.COLOR_BGR2RGB))
        return out

    @property
    def bytes_used(self):
        return sum(len(blob) for _, blob in self.frames)

    @property
    def span_sec(self):
        if len(self.frames) < 2:
            return 0.0
        return self.frames[-1][0] - self.frames[0][0]

    def clear(self):
        self.frames.clear()
        self.floor_sec = None


def build_store(cfg):
    clips = cfg.get("clips", {})
    return ClipStore(
        root=cfg.path(clips.get("dir", "data/clips")),
        max_total_gb=clips.get("max_total_gb", 5.0),
        max_clip_sec=clips.get("max_clip_sec", 60.0),
        project_root=cfg.root,
    )


def clips_enabled(cfg):
    return bool(cfg.get("clips", {}).get("enabled", False))


def describe_store(store):
    total = store.total_bytes()
    return "%s (%.2f GB of %.1f GB budget)" % (
        store.root,
        total / BYTES_PER_GB,
        store.max_total_bytes / BYTES_PER_GB,
    )


def touch(path):
    """Refresh mtime so a just-written clip is not the first GC victim."""
    now = time.time()
    try:
        os.utime(path, (now, now))
    except OSError:
        pass

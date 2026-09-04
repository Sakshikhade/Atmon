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
# QuickTime renders as a GREEN FRAME -- the data is fine (measured: normal pixel
# statistics, decodes correctly through OpenCV), but the player cannot show it.
# A clip nobody can watch is not a saved clip.
CODECS = ("avc1", "mp4v")


def open_writer(path, fps, size, codecs=CODECS):
    """VideoWriter using the first codec this build can actually open.

    Returns (writer, codec). Raises if none work.
    """
    import cv2

    for codec in codecs:
        writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*codec), float(fps), size)
        if writer.isOpened():
            return writer, codec
        writer.release()
    raise RuntimeError(
        "no usable video codec for %s -- tried %s" % (path, ", ".join(codecs)))


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
    """Write RGB uint8 frames to an mp4. Returns the path, or None if empty."""
    import cv2

    if not len(frames):
        return None
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
    size = (
        int(capture.get(cv2.CAP_PROP_FRAME_WIDTH)),
        int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT)),
    )

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
                    continue
                if span["writer"] is None:
                    path = store.path_for(source_id, span["event_id"])
                    span["writer"], _ = open_writer(path, fps, size)
                    written[span["event_id"]] = path
                span["writer"].write(frame)
    finally:
        for span in spans:
            if span["writer"] is not None:
                span["writer"].release()
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

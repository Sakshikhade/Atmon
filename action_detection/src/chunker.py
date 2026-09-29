"""Video reading and non-overlapping chunking (spec 5).

Chunks are yielded lazily: a 30-minute video at 8 fps is ~14k frames, which does
not fit in memory at full resolution, so only one chunk is resident at a time.

Backends are tried in order decord -> PyAV -> torchvision -> OpenCV. decord is
preferred per the spec but has no official Apple Silicon wheel, hence the chain.
All backends return uint8 RGB [T, H, W, 3] so the encoder sees identical input
whichever one ran.
"""

import os

import numpy as np


class VideoReadError(RuntimeError):
    pass


def _probe_opencv(path):
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise VideoReadError("could not open %s" % path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    n_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    cap.release()
    if fps <= 0:
        raise VideoReadError("could not determine fps for %s" % path)
    return fps, n_frames


def _probe_pyav(path):
    import av

    with av.open(path) as container:
        stream = container.streams.video[0]
        fps = float(stream.average_rate or 0.0)
        n_frames = int(stream.frames or 0)
        if n_frames <= 0 and stream.duration and stream.time_base:
            n_frames = int(float(stream.duration * stream.time_base) * fps)
    if fps <= 0:
        raise VideoReadError("could not determine fps for %s" % path)
    return fps, n_frames


def probe_video(path):
    """(native_fps, n_frames, duration_sec). duration may be 0 if unknown.

    Probes with the same backend preference iter_frames uses. That ordering is
    not cosmetic: PyAV and OpenCV each bundle their own ffmpeg, and macOS warns
    loudly ("mysterious crashes") when both land in one process. Probing with
    OpenCV while decoding with PyAV used to guarantee that collision even for
    read-only work.
    """
    if not os.path.exists(path):
        raise VideoReadError("no such video: %s" % path)

    fps = n_frames = 0
    for probe in (_probe_decord, _probe_pyav, _probe_opencv):
        try:
            fps, n_frames = probe(path)
            break
        except Exception:  # noqa: BLE001 -- try the next backend
            continue
    if fps <= 0:
        raise VideoReadError("no backend could probe %s" % path)

    duration = n_frames / fps if fps > 0 and n_frames > 0 else 0.0
    return fps, n_frames, duration


def _probe_decord(path):
    import decord

    reader = decord.VideoReader(path)
    fps = float(reader.get_avg_fps())
    n_frames = len(reader)
    del reader
    if fps <= 0:
        raise VideoReadError("could not determine fps for %s" % path)
    return fps, n_frames


def _iter_frames_decord(path, working_fps):
    import decord

    reader = decord.VideoReader(path)
    native_fps = float(reader.get_avg_fps())
    n_frames = len(reader)
    n_out = int(np.floor(n_frames / native_fps * working_fps))
    if n_out <= 0:
        return
    # Nearest native frame for each output timestamp.
    idx = np.rint(np.arange(n_out) / working_fps * native_fps).astype(np.int64)
    idx = np.clip(idx, 0, n_frames - 1)
    # Batch the fetch so decord seeks once per group rather than per frame.
    for start in range(0, len(idx), 64):
        block = reader.get_batch(idx[start : start + 64]).asnumpy()
        for frame in block:
            yield frame.astype(np.uint8)


def _iter_frames_pyav(path, working_fps):
    import av

    container = av.open(path)
    stream = container.streams.video[0]
    stream.thread_type = "AUTO"
    step = 1.0 / working_fps
    next_t = 0.0
    try:
        for frame in container.decode(stream):
            t = float(frame.time or 0.0)
            if t + 1e-9 < next_t:
                continue
            yield frame.to_ndarray(format="rgb24").astype(np.uint8)
            # Skip ahead past any frames this timestamp already covered.
            while next_t <= t + 1e-9:
                next_t += step
    finally:
        container.close()


def _iter_frames_opencv(path, working_fps):
    import cv2

    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        raise VideoReadError("could not open %s" % path)
    native_fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if native_fps <= 0:
        cap.release()
        raise VideoReadError("could not determine fps for %s" % path)

    step = native_fps / working_fps
    try:
        i = 0
        next_i = 0.0
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if i >= next_i - 1e-9:
                yield cv2.cvtColor(frame, cv2.COLOR_BGR2RGB).astype(np.uint8)
                next_i += step
            i += 1
    finally:
        cap.release()


def _iter_frames_torchvision(path, working_fps):
    from torchvision.io import read_video

    frames, _, info = read_video(path, output_format="THWC", pts_unit="sec")
    native_fps = float(info.get("video_fps") or 0.0)
    arr = frames.numpy().astype(np.uint8)
    if native_fps <= 0:
        raise VideoReadError("could not determine fps for %s" % path)
    n_out = int(np.floor(len(arr) / native_fps * working_fps))
    idx = np.rint(np.arange(n_out) / working_fps * native_fps).astype(np.int64)
    idx = np.clip(idx, 0, len(arr) - 1)
    for i in idx:
        yield arr[i]


_BACKENDS = (
    ("decord", _iter_frames_decord),
    ("pyav", _iter_frames_pyav),
    ("torchvision", _iter_frames_torchvision),
    ("opencv", _iter_frames_opencv),
)


def iter_frames(path, working_fps, backend=None):
    """Yield uint8 RGB frames resampled to working_fps.

    Tries backends in order until one produces a first frame; a backend that
    fails mid-stream is NOT retried with another, since partial output would be
    silently concatenated with a second pass.
    """
    candidates = _BACKENDS
    if backend and backend != "auto":
        candidates = [(n, f) for n, f in _BACKENDS if n == backend]
        if not candidates:
            raise ValueError(
                "unknown video backend %r; choose from %s"
                % (backend, ", ".join(n for n, _ in _BACKENDS))
            )

    errors = []
    for name, fn in candidates:
        try:
            stream = fn(path, working_fps)
            first = next(stream, None)
        except Exception as exc:  # noqa: BLE001
            errors.append("%s: %s" % (name, exc))
            continue
        if first is None:
            errors.append("%s: produced no frames" % name)
            continue
        yield first
        for frame in stream:
            yield frame
        return

    raise VideoReadError("no video backend could read %s\n  %s" % (path, "\n  ".join(errors)))


def iter_chunks(path, working_fps, chunk_sec, backend=None):
    """Yield (start_sec, frames [T, H, W, 3]) for non-overlapping chunks.

    Each frame belongs to exactly one chunk and is decoded once (spec 5.2). A
    trailing partial chunk is dropped -- it would be encoded at a different
    effective frame rate than every other chunk.
    """
    per_chunk = max(1, int(round(working_fps * chunk_sec)))
    buf = []
    index = 0
    for frame in iter_frames(path, working_fps, backend):
        buf.append(frame)
        if len(buf) == per_chunk:
            yield index * chunk_sec, np.stack(buf)
            buf = []
            index += 1


def video_id_for(path):
    return os.path.splitext(os.path.basename(path))[0]

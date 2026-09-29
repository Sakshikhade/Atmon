"""Synthetic video fixtures (numpy only; cv2 needed just to write files).

You have no reference clips or labeled eval set yet, so these stand in: a bright
square that oscillates (the "action"), drifts, or sits still (background). They
exist to exercise plumbing end to end, not to say anything about how the real
encoder will behave on real behaviour.
"""

import json
import os

import numpy as np

FRAME_H = 64
FRAME_W = 64
SQUARE = 12


def _frame(cx, cy, noise_rng=None):
    img = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)
    x0 = int(np.clip(cx - SQUARE // 2, 0, FRAME_W - SQUARE))
    y0 = int(np.clip(cy - SQUARE // 2, 0, FRAME_H - SQUARE))
    img[y0 : y0 + SQUARE, x0 : x0 + SQUARE] = 235
    if noise_rng is not None:
        noise = noise_rng.integers(-8, 9, size=img.shape, dtype=np.int16)
        img = np.clip(img.astype(np.int16) + noise, 0, 255).astype(np.uint8)
    return img


def synthetic_clip(kind, n_frames, fps=8.0, freq=2.0, seed=0, phase=0.0):
    """kind: 'oscillate' | 'static' | 'drift'. Returns uint8 RGB [T, H, W, 3]."""
    rng = np.random.default_rng(seed)
    t = np.arange(n_frames) / float(fps)
    cy = np.full(n_frames, FRAME_H / 2.0)

    if kind == "oscillate":
        cx = FRAME_W / 2.0 + (FRAME_W / 3.5) * np.sin(2 * np.pi * freq * t + phase)
    elif kind == "static":
        cx = np.full(n_frames, FRAME_W / 2.0)
    elif kind == "drift":
        cx = np.linspace(SQUARE, FRAME_W - SQUARE, n_frames)
    else:
        raise ValueError("unknown clip kind %r" % kind)

    return np.stack([_frame(x, y, rng) for x, y in zip(cx, cy)])


def synthetic_timeline(segments, fps=8.0, seed=0):
    """Concatenate (kind, duration_sec) segments into one clip plus its labels.

    Returns (frames, events) where events are {"class", "start", "end"} for every
    non-background segment -- i.e. ready-made ground truth.
    """
    frames = []
    events = []
    t = 0.0
    for i, (kind, duration, class_name) in enumerate(segments):
        n = int(round(duration * fps))
        frames.append(synthetic_clip(kind, n, fps=fps, seed=seed + i))
        if class_name is not None:
            events.append({"class": class_name, "start": t, "end": t + n / fps})
        t += n / fps
    return np.concatenate(frames), events


def write_video(path, frames, fps=8.0):
    """Write frames to an mp4. The only fixture helper that needs OpenCV."""
    import cv2

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    h, w = frames.shape[1:3]
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    if not writer.isOpened():
        raise RuntimeError("could not open a writer for %s" % path)
    try:
        for frame in frames:
            writer.write(cv2.cvtColor(frame, cv2.COLOR_RGB2BGR))
    finally:
        writer.release()
    return path


def write_labels(path, video_id, duration, events):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"video_id": video_id, "duration": duration, "events": events}, fh, indent=2)
    return path


def encode_timeline(encoder, frames, fps=8.0, chunk_sec=1.0):
    """Chunk a frame array and encode it, mirroring features.encode_video().

    In-memory equivalent of the offline cache path, so tests can exercise the
    scoring chain without a video file or a decoder.
    """
    per_chunk = max(1, int(round(fps * chunk_sec)))
    clips, starts = [], []
    for i in range(0, len(frames) - per_chunk + 1, per_chunk):
        clips.append(frames[i : i + per_chunk])
        starts.append(len(starts) * chunk_sec)
    feats = encoder.encode_clips(clips)
    return feats, np.asarray(starts, dtype=np.float32)

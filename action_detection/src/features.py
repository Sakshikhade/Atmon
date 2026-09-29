"""Chunk encoding with an on-disk cache (spec 5).

Cache contract (spec 4) plus one addition: frames_per_clip. It is keyed on as
well, because changing it changes every feature vector while leaving model_id,
working_fps and chunk_sec untouched -- exactly the "stale features produce
plausible-looking garbage" failure pitfall 14.6 warns about.
"""

import os

import numpy as np

from src.chunker import iter_chunks, video_id_for
from src.crop import apply_box, crop_tag


def cache_path(cfg, video_id):
    """Keyed on the BACKBONE as well as the crop.

    Without the backbone in the path, switching encoders silently reuses the
    previous one's features -- the pitfall 14.6 failure, and one that looks like
    a result rather than an error.
    """
    from src.crop import crop_tag

    tag = crop_tag(cfg)
    suffix = "" if tag == "none" else "__%s" % tag
    return cfg.path("cache", "features",
                    "%s__%s%s.npz" % (cfg.get("backbone", "vjepa"), video_id, suffix))


def _is_current(data, cfg):
    """A cache entry is reusable only if every keyed parameter matches.

    crop_tag is keyed on too: cropping changes every feature vector while
    model_id, fps, chunk_sec and frames_per_clip all stay put -- exactly the
    silent-stale-cache failure pitfall 14.6 warns about.
    """
    from src.crop import crop_tag

    try:
        return (
            str(data["model_id"]) == str(cfg["model_id"])
            and float(data["fps"]) == float(cfg["working_fps"])
            and float(data["chunk_sec"]) == float(cfg["chunk_sec"])
            and int(data["frames_per_clip"]) == int(cfg["frames_per_clip"])
            and str(data["crop_tag"]) == crop_tag(cfg)
            and str(data["backbone"]) == str(cfg.get("backbone", "vjepa"))
        )
    except KeyError:
        return False


def load_cache(cfg, video_id):
    """Return the cached features, or None if absent or stale."""
    path = cache_path(cfg, video_id)
    if not os.path.exists(path):
        return None
    with np.load(path, allow_pickle=False) as data:
        if not _is_current(data, cfg):
            return None
        return {
            "feats": data["feats"].astype(np.float32),
            "starts": data["starts"].astype(np.float32),
            "chunk_sec": float(data["chunk_sec"]),
            "fps": float(data["fps"]),
            "model_id": str(data["model_id"]),
        }


def encode_video(cfg, encoder, video_path, force=False, batch_size=4, progress=True,
                 crop_boxes=None):
    """Encode a video's chunks to the cache, or reuse an existing entry.

    Returns the same dict shape as load_cache().
    """
    video_id = video_id_for(video_path)
    if not force:
        cached = load_cache(cfg, video_id)
        if cached is not None:
            return cached

    working_fps = float(cfg["working_fps"])
    chunk_sec = float(cfg["chunk_sec"])

    feats = []
    starts = []
    pending = []
    pending_starts = []

    def flush():
        if pending:
            feats.append(encoder.encode_clips(pending))
            starts.extend(pending_starts)
            pending.clear()
            pending_starts.clear()

    iterator = iter_chunks(video_path, working_fps, chunk_sec, backend=cfg.get("video_backend"))
    if progress:
        try:
            from tqdm import tqdm

            iterator = tqdm(iterator, desc="encoding %s" % video_id, unit="chunk")
        except ImportError:
            pass

    for index, (start_sec, frames) in enumerate(iterator):
        if crop_boxes is not None and index < len(crop_boxes):
            frames = apply_box(frames, crop_boxes[index])
        pending.append(frames)
        pending_starts.append(start_sec)
        if len(pending) >= batch_size:
            flush()
    flush()

    if not feats:
        raise RuntimeError(
            "no chunks encoded from %s -- the video is shorter than chunk_sec (%.2fs)"
            % (video_path, chunk_sec)
        )

    result = {
        "feats": np.concatenate(feats, axis=0).astype(np.float32),
        "starts": np.asarray(starts, dtype=np.float32),
        "chunk_sec": chunk_sec,
        "fps": working_fps,
        "model_id": str(cfg["model_id"]),
    }

    out = cache_path(cfg, video_id)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(
        out,
        feats=result["feats"],
        starts=result["starts"],
        chunk_sec=np.float32(chunk_sec),
        fps=np.float32(working_fps),
        model_id=str(cfg["model_id"]),
        frames_per_clip=np.int32(cfg["frames_per_clip"]),
        crop_tag=crop_tag(cfg),
        backbone=str(cfg.get("backbone", "vjepa")),
    )
    return result


def ensure_features(cfg, video_path, encoder_factory, force=False, crop_boxes=None):
    """Load cached features, building the encoder only if there is work to do.

    detect.py calls this so a cached video never pays the model load (spec 13).
    """
    video_id = video_id_for(video_path)
    if not force:
        cached = load_cache(cfg, video_id)
        if cached is not None:
            return cached
    return encode_video(cfg, encoder_factory(), video_path, force=force,
                        crop_boxes=crop_boxes)

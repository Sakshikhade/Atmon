"""One place that turns a video into a score grid.

detect.py, calibrate.py and check_separation.py all need the same thing: cached
embeddings, optionally cached pose, fused into one grid. Doing that in three
places is how the streams drift apart.
"""

import os

from src.chunker import video_id_for
from src.encoder import build_encoder
from src.features import ensure_features
from src.scoring import combined_grid


def pose_available(cfg):
    return bool(cfg.get("pose", {}).get("enabled", False))


def load_pose_for(cfg, video_path, video_id, force=False):
    """Cached pose sequences plus templates, or (None, None) when disabled.

    Pose keypoints are needed for TWO independent reasons: the pose stream, and
    the crop boxes. Cropping without the pose stream is a legitimate
    configuration, so the keypoints are extracted whenever EITHER wants them and
    the templates are returned only when the stream is on.

    A missing template bank is not fatal: the run continues on the embedding
    stream alone and says so, rather than failing after the expensive part.
    """
    from src.crop import enabled as crop_enabled

    stream_on = pose_available(cfg)
    if not stream_on and not crop_enabled(cfg):
        return None, None

    from src.pose import encode_video_pose, load_pose_templates

    templates = None
    if stream_on:
        templates = load_pose_templates(cfg)
        if not templates:
            print("  pose enabled but no template bank -- run build_prototypes.py; "
                  "continuing on the embedding stream alone")
        else:
            missing = [c for c in cfg.class_names if c not in templates]
            if missing:
                print("  pose templates missing for: %s (those classes use embeddings only)"
                      % ", ".join(missing))

    bundle = encode_video_pose(cfg, video_path, video_id, force=force)
    return bundle, templates


def load_hands_for(cfg, video_path, video_id, force=False):
    """Cached hand sequences plus templates, or (None, None) when disabled."""
    if not bool(cfg.get("hands", {}).get("enabled", False)):
        return None, None

    from src.hands import encode_video_hands, load_hand_templates

    templates = load_hand_templates(cfg)
    if not templates:
        print("  hands enabled but no template bank -- run build_prototypes.py; "
              "continuing without the hand stream")
        return None, None
    return encode_video_hands(cfg, video_path, video_id, force=force), templates


def grid_for_video(cfg, bank, video_path, force=False, verbose=True):
    """(video_id, feats_bundle, class_names, fused_grid, parts).

    Pose runs FIRST because the crop boxes come from it -- the appearance stream
    cannot be encoded until the crop is known.
    """
    from src.crop import enabled as crop_enabled

    video_id = video_id_for(video_path)
    pose_bundle, templates = load_pose_for(cfg, video_path, video_id, force=force)
    hand_bundle, hand_templates = load_hands_for(cfg, video_path, video_id, force=force)

    crop_boxes = None
    if crop_enabled(cfg):
        if pose_bundle is not None and pose_bundle.get("boxes") is not None:
            crop_boxes = pose_bundle["boxes"]
        else:
            print("  crop enabled but no pose boxes available -- encoding full frames")

    feats = ensure_features(cfg, video_path, lambda: build_encoder(cfg), force=force,
                            crop_boxes=crop_boxes)
    class_names, grid, parts = combined_grid(
        cfg, bank, feats, pose_bundle, templates, hand_bundle, hand_templates
    )

    if verbose:
        active = [k for k in ("vjepa", "pose", "hands") if parts.get(k) is not None]
        print("  %-22s %d chunks, streams: %s%s"
              % (video_id, len(feats["starts"]), " + ".join(active),
                 ", cropped" if crop_boxes is not None else ""))
    return video_id, feats, class_names, grid, parts


def describe_fusion(cfg, class_names, available=None):
    """Weights actually in force. `available` must list the streams that will be
    fused -- defaulting to a subset printed weights that were not the ones used.
    """
    from src.scoring import stream_weights

    if available is None:
        available = ["vjepa"]
        if cfg.get("pose", {}).get("enabled", False):
            available.append("pose")
        if cfg.get("hands", {}).get("enabled", False):
            available.append("hands")
    rows = []
    for name in class_names:
        weights = stream_weights(cfg, name, list(available))
        rows.append("    %-16s %s" % (
            name, "  ".join("%s %.2f" % (k, weights[k]) for k in sorted(weights))))
    return "\n".join(rows)


def clean_stale_pose_cache(cfg):
    """Pose cache keys on fps/chunk_sec only; drop entries that no longer match."""
    directory = cfg.path("cache", "pose")
    if not os.path.isdir(directory):
        return 0
    from src.pose import load_pose_cache

    removed = 0
    for name in os.listdir(directory):
        if not name.endswith(".npz"):
            continue
        if load_pose_cache(cfg, os.path.splitext(name)[0]) is None:
            os.remove(os.path.join(directory, name))
            removed += 1
    return removed

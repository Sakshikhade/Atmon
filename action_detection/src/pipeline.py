"""One place that turns a video into a score grid.

detect.py, calibrate.py and check_separation.py all need the same thing: cached
embeddings, optionally cached pose, fused into one grid. Doing that in three
places is how the streams drift apart.
"""

from src.chunker import video_id_for
from src.encoder import build_encoder
from src.features import ensure_features
from src.scoring import combined_grid


def pose_available(cfg):
    return bool(cfg.get("pose", {}).get("enabled", False))


def load_pose_for(cfg, video_path, video_id, force=False):
    """Cached pose sequences plus templates, or (None, None) when unused.

    Keypoints are needed for three independent reasons:
      - the DTW pose score stream (`pose.enabled`)
      - crop boxes (`crop.enabled`)
      - offline wrist-near-ear confirmation (`require_wrist_near_ear` on a class)

    Templates are returned only when the DTW stream is on. Gate/crop-only runs
    still get a sequences bundle so offline grouping can match live FP control.

    A missing template bank or landmarker file is not fatal for gate-only: the
    run continues on embeddings and says so (live hard-fails when the gate is
    required and the model is missing -- that path is separate).
    """
    from src.crop import enabled as crop_enabled
    from src.pose import classes_needing_wrist_gate

    stream_on = pose_available(cfg)
    gate_on = bool(classes_needing_wrist_gate(cfg))
    crop_on = crop_enabled(cfg)
    if not stream_on and not crop_on and not gate_on:
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

    try:
        bundle = encode_video_pose(cfg, video_path, video_id, force=force)
    except Exception as exc:  # noqa: BLE001 -- landmarker / decode; fail soft offline
        if stream_on or crop_on:
            raise
        print("  wrist gate needs pose landmarks but extraction failed (%s); "
              "continuing without the offline gate" % exc)
        return None, None
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
    # Sequences for the offline wrist gate (already normalize_pose'd). Not a
    # score stream -- underscore so describe_fusion / diagnostics ignore it.
    if pose_bundle is not None and pose_bundle.get("sequences") is not None:
        parts["_pose_sequences"] = pose_bundle["sequences"]

    if verbose:
        active = [k for k in ("vjepa", "pose", "hands") if parts.get(k) is not None]
        gate_note = ", wrist-gate" if parts.get("_pose_sequences") is not None else ""
        print("  %-22s %d chunks, streams: %s%s%s"
              % (video_id, len(feats["starts"]), " + ".join(active),
                 ", cropped" if crop_boxes is not None else "", gate_note))
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

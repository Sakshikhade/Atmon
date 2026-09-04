"""Reference clips -> prototype bank (spec 6).

Augmented variants are kept individually and never collapsed to a class mean
(spec 4): the top-k similarity in scoring.py depends on having the spread.

Every variant is encoded through Encoder.encode_clips, the same call the target
path uses. That is the whole point of Phase 2's acceptance test -- a reference
scored against its own bank must come back near 1.0, and it will not if these
two paths differ by so much as a resize (spec 14.1).
"""

import os

import numpy as np

from src.chunker import iter_frames
from src.crop import enabled as crop_enabled

# Reserved keys in the npz. Class names may not start with "__".
W_BASE_KEY = "__w_base_sec__"
_RESERVED = (W_BASE_KEY,)


def bank_path(cfg):
    """One bank per backbone -- prototypes from one encoder are meaningless
    against another's features."""
    return cfg.path("cache", "prototypes_%s.npz" % cfg.get("backbone", "vjepa"))


def load_reference_clip(path, working_fps, backend=None):
    """A reference clip, whole, as uint8 RGB [T, H, W, 3]."""
    frames = list(iter_frames(path, working_fps, backend))
    if not frames:
        raise RuntimeError("reference clip produced no frames: %s" % path)
    return np.stack(frames)


def _speed(clip, factor):
    """Resample along time. factor > 1 plays faster (fewer frames)."""
    n_out = max(2, int(round(len(clip) / factor)))
    idx = np.clip(np.rint(np.linspace(0, len(clip) - 1, n_out)).astype(np.int64), 0, len(clip) - 1)
    return clip[idx]


def _temporal_crop(clip, keep_ratio, rng):
    """Keep a contiguous keep_ratio of the clip, starting at a jittered offset."""
    n_keep = max(2, int(round(len(clip) * keep_ratio)))
    if n_keep >= len(clip):
        return clip
    start = int(rng.integers(0, len(clip) - n_keep + 1))
    return clip[start : start + n_keep]


def _spatial_crop(clip, keep_ratio, rng):
    """Crop 90-100% of the frame at a jittered position, then resize back."""
    import cv2

    t, h, w = clip.shape[:3]
    ch, cw = max(8, int(h * keep_ratio)), max(8, int(w * keep_ratio))
    if ch >= h and cw >= w:
        return clip
    top = int(rng.integers(0, h - ch + 1))
    left = int(rng.integers(0, w - cw + 1))
    cropped = clip[:, top : top + ch, left : left + cw]
    return np.stack([cv2.resize(f, (w, h), interpolation=cv2.INTER_LINEAR) for f in cropped])


def _flip(clip):
    return clip[:, :, ::-1]


def _degrade(clip, quality=45):
    """Light JPEG degradation, narrowing a reference-vs-target quality gap."""
    import cv2

    out = []
    for frame in clip:
        ok, buf = cv2.imencode(".jpg", frame[:, :, ::-1], [int(cv2.IMWRITE_JPEG_QUALITY), quality])
        out.append(cv2.imdecode(buf, cv2.IMREAD_COLOR)[:, :, ::-1] if ok else frame)
    return np.stack(out).astype(np.uint8)


def select_reference_window(clip, n_keep):
    """Keep the highest-sustained-motion contiguous window of n_keep frames.

    Long reference takes often start before the action (desk, still face) or
    linger after it. Using the middle of the clip is a common failure mode for
    nodding / twirling. Frame-to-frame L1 motion is cheap and good enough to
    find where the subject actually moved.
    """
    n = len(clip)
    if n_keep >= n or n_keep < 2:
        return clip, 0
    # Mean abs frame diff per step; pad first with a copy of the second.
    diffs = np.mean(np.abs(clip[1:].astype(np.float32) - clip[:-1].astype(np.float32)),
                    axis=(1, 2, 3))
    # Cumulative sum for O(1) window sums over (n_keep - 1) diffs covering n_keep frames.
    csum = np.concatenate([[0.0], np.cumsum(diffs)])
    window = n_keep - 1
    scores = csum[window:] - csum[:-window]
    start = int(np.argmax(scores))
    return clip[start : start + n_keep], start


def build_variants(clip, class_cfg, proto_cfg, rng):
    """Deterministic recipe of 10-15 augmented variants for one reference clip.

    Horizontal flip is per-class: mirroring changes the meaning of a chirally
    defined action, so allow_flip gates it (spec 6, pitfall 14.7).
    """
    allow_flip = bool(class_cfg.get("allow_flip", False))
    speeds = list(proto_cfg["speeds"])
    t_min = float(proto_cfg["temporal_crop_min_ratio"])
    s_min = float(proto_cfg["spatial_crop_min_ratio"])

    variants = []

    # 1 identity + one per non-unit speed
    variants.append(("identity", clip))
    for sp in speeds:
        if abs(sp - 1.0) > 1e-9:
            variants.append(("speed%.2f" % sp, _speed(clip, sp)))

    # Two temporal crops per speed, jittered start, >= 70% duration kept.
    for sp in speeds:
        base = clip if abs(sp - 1.0) < 1e-9 else _speed(clip, sp)
        for k in range(2):
            keep = float(rng.uniform(t_min, 1.0))
            variants.append(("tcrop%.2f_%d" % (sp, k), _temporal_crop(base, keep, rng)))

    # Two spatial crop jitters, 90-100% of frame.
    for k in range(2):
        keep = float(rng.uniform(s_min, 1.0))
        variants.append(("scrop%d" % k, _spatial_crop(clip, keep, rng)))

    if allow_flip:
        variants.append(("flip", _flip(clip)))
        keep = float(rng.uniform(t_min, 1.0))
        variants.append(("flip_tcrop", _flip(_temporal_crop(clip, keep, rng))))

    if proto_cfg.get("degrade", True):
        variants.append(("degrade", _degrade(clip)))

    target = int(proto_cfg.get("variants_per_class", 12))
    return variants[:target] if len(variants) > target else variants


def build_bank(cfg, encoder, references_dir=None, verbose=True):
    """Encode every class's reference clips into the prototype bank.

    Returns (bank, w_base_sec) where bank maps class name -> [n_variants, D].
    """
    references_dir = references_dir or cfg.path("data", "references")
    proto_cfg = cfg["prototypes"]
    rng = np.random.default_rng(int(proto_cfg.get("seed", 0)))
    working_fps = float(cfg["working_fps"])

    bank = {}
    durations = []
    # Below this, a clip is frame-REPEATED to fill the encoder's grid while every
    # target chunk is not, so similarity degrades for reasons unrelated to the
    # action. See tests/stub_encoder.py for the measured effect.
    min_useful_sec = float(cfg["frames_per_clip"]) / working_fps
    # Ceiling: W_base is the median reference duration (spec 7.1). A 15s ref
    # inflates windows and min_duration so short live actions (nods) never stick.
    max_useful_sec = float(proto_cfg.get("max_reference_sec", 4.0))
    too_short = []
    too_long = []

    skipped = []
    for class_name in cfg.class_names:
        if class_name.startswith("__"):
            raise ValueError("class names may not start with '__' (reserved): %r" % class_name)

        class_dir = os.path.join(references_dir, class_name)
        if not os.path.isdir(class_dir):
            skipped.append((class_name, "missing directory"))
            continue
        clips = sorted(
            os.path.join(class_dir, f)
            for f in os.listdir(class_dir)
            if not f.startswith(".") and os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".avi", ".mkv")
        )
        if not clips:
            # Config may list a class whose refs were deleted; skip instead of
            # failing the whole bank rebuild (demo UI hits this often).
            skipped.append((class_name, "empty directory"))
            continue

        class_cfg = cfg.class_cfg(class_name)
        vectors = []
        for clip_path in clips:
            clip = load_reference_clip(clip_path, working_fps, cfg.get("video_backend"))
            # Crop the reference by the SAME rule the targets get, or the two
            # encoding paths diverge on framing (spec 14.1).
            if crop_enabled(cfg):
                from src.crop import apply_box
                from src.pose import reference_box

                box = reference_box(cfg, clip_path)
                clip = apply_box(clip, box)
                if verbose:
                    print("  %-16s %-22s crop box %s"
                          % (class_name, os.path.basename(clip_path),
                             " ".join("%.2f" % v for v in box)))
            duration = len(clip) / working_fps
            if duration > max_useful_sec:
                n_keep = max(2, int(round(max_useful_sec * working_fps)))
                clip, start_i = select_reference_window(clip, n_keep)
                too_long.append((clip_path, duration, len(clip) / working_fps, start_i / working_fps))
                duration = len(clip) / working_fps
            durations.append(duration)
            if duration < min_useful_sec:
                too_short.append((clip_path, duration))
            variants = build_variants(clip, class_cfg, proto_cfg, rng)
            vectors.append(encoder.encode_clips([v for _, v in variants]))
            if verbose:
                print("  %s: %s -> %d variants" % (class_name, os.path.basename(clip_path), len(variants)))

        bank[class_name] = np.concatenate(vectors, axis=0).astype(np.float32)

    if skipped and verbose:
        print("\n  Skipping %d class(es) with no reference clips:" % len(skipped))
        for name, reason in skipped:
            print("    %-16s %s" % (name, reason))

    if not bank:
        raise FileNotFoundError(
            "no reference clips found under %s -- record at least one class first"
            % references_dir
        )

    if too_long and verbose:
        print(
            "\n  NOTE: %d reference clip(s) longer than %.1fs were trimmed to the "
            "highest-motion window (max_reference_sec). W_base follows the trimmed length."
            % (len(too_long), max_useful_sec)
        )
        for path, before, after, start in too_long:
            print("    %.1fs -> %.1fs @ t=%.1fs  %s"
                  % (before, after, start, os.path.basename(path)))

    if too_short and verbose:
        print(
            "\n  WARNING: %d reference clip(s) are shorter than %.2fs "
            "(frames_per_clip %d / working_fps %g)."
            % (len(too_short), min_useful_sec, cfg["frames_per_clip"], working_fps)
        )
        for path, duration in too_short:
            print("    %.2fs  %s" % (duration, os.path.basename(path)))
        print(
            "  Clips below that are padded with repeated frames while every target\n"
            "  chunk is not, so similarity suffers for reasons unrelated to the\n"
            "  action. Re-record them at %.1fs or longer." % min_useful_sec
        )

    # W_base is the median reference clip duration (spec 7.1).
    w_base_sec = float(np.median(durations)) if durations else float(cfg["chunk_sec"])
    return bank, w_base_sec


def save_bank(cfg, bank, w_base_sec):
    out = bank_path(cfg)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    payload = dict(bank)
    payload[W_BASE_KEY] = np.float32(w_base_sec)
    np.savez(out, **payload)
    return out


def load_bank(cfg):
    """(bank, w_base_sec). Raises if build_prototypes.py has not been run."""
    path = bank_path(cfg)
    if not os.path.exists(path):
        raise SystemExit(
            "no prototype bank at %s -- run scripts/build_prototypes.py first" % path
        )
    with np.load(path, allow_pickle=False) as data:
        bank = {k: data[k].astype(np.float32) for k in data.files if k not in _RESERVED}
        w_base_sec = float(data[W_BASE_KEY]) if W_BASE_KEY in data.files else None
    if not bank:
        raise SystemExit("prototype bank at %s contains no classes" % path)
    return bank, w_base_sec


def self_similarity(bank, class_name):
    """Phase 2 acceptance: a class's variants scored against their own bank.

    Returns the mean top-1-excluding-self similarity. Near 1.0 means the two
    encoding paths agree; well below means they have diverged (spec 6).
    """
    vecs = bank[class_name]
    sim = vecs @ vecs.T
    np.fill_diagonal(sim, -np.inf)
    return float(np.mean(np.max(sim, axis=1)))

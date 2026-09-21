"""Pose stream: frozen keypoints + DTW template matching (spec 11.3).

Why this exists. V-JEPA mean-pools every patch token to one vector, so an action
occupying a small part of the frame -- a hand in the hair -- is averaged against
the unchanged room and disappears. Measured on real footage, hair-twirling
scored BELOW background. Pose does not have that failure mode: it throws the
room away entirely and keeps the geometry, which is what these behaviours
actually are.

Nothing here is trained. MediaPipe is frozen, the "template" is just the
normalized keypoint sequence of a reference clip, and matching is DTW. Adding a
new action is still "add a reference clip" -- the property the whole system is
built around.

Fusion is at SCORE level, per class (spec 11.3):

    score_c = alpha_c * z(sim_vjepa) + (1 - alpha_c) * z(sim_pose)

Never concatenate pose and V-JEPA feature vectors. With one example per class
the relative scaling cannot be learned and one modality silently dominates --
late fusion of scalar scores is what PoseConv3D validated.
"""

import os

import numpy as np

# MediaPipe pose landmark indices used for normalization and matching.
L_SHOULDER, R_SHOULDER = 11, 12
L_HIP, R_HIP = 23, 24
# Upper body only: hips and legs contribute nothing to hand-to-head behaviours
# and add noise when the lower body is out of frame or occluded by a desk.
UPPER_BODY = list(range(0, 25))

# Drawable skeleton, in UPPER_BODY-local indices. Torso + arms + hands only.
#
# Deliberately NO face-mesh edges: MediaPipe indices 1-10 are eyes, ears and
# mouth corners, and joining them produces a scribble across the face that
# reads as clutter rather than information. The head is represented by the nose
# point alone (drawn as a joint, not an edge).
POSE_EDGES = [
    (11, 12),                      # shoulders
    (11, 13), (13, 15),            # left upper arm, forearm
    (12, 14), (14, 16),            # right upper arm, forearm
    (15, 17), (15, 19), (15, 21),  # left hand
    (16, 18), (16, 20), (16, 22),  # right hand
    (11, 23), (12, 24), (23, 24),  # torso down to hips
]


class PoseExtractor:
    """MediaPipe PoseLandmarker, frozen, IMAGE mode.

    Reuses the pose_landmarker.task already in the parent repository rather than
    downloading another copy.
    """

    def __init__(self, model_path=None, min_confidence=0.5):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        model_path = model_path or self._default_model_path()
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                "pose model not found at %s -- point pose.model_path at a\n"
                "pose_landmarker.task file (the parent AAMAS repo has one)." % model_path
            )

        # CPU delegate, matching the parent AAMAS repo. Defensive rather than
        # load-bearing: pose is cheap on CPU next to a ViT-L forward pass, and
        # there is no reason to contend for the GPU.
        #
        # It is NOT what fixes the macOS abort described in requirements.txt --
        # mediapipe 1.0.x aborts inside TensorsToDetectionsCalculator's Metal
        # helper regardless of this setting, and regardless of whether torch is
        # in the process. Only the version pin fixes that.
        options = vision.PoseLandmarkerOptions(
            base_options=python.BaseOptions(
                model_asset_path=model_path,
                delegate=python.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.IMAGE,
            min_pose_detection_confidence=min_confidence,
            min_pose_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self.detector = vision.PoseLandmarker.create_from_options(options)
        self._mp = mp

    @staticmethod
    def _default_model_path():
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        candidates = [
            os.path.join(here, "models", "pose_landmarker.task"),
            os.path.join(here, "..", "pose_landmarker.task"),
            os.path.join(here, "..", "atmos-proj", "pose_landmarker.task"),
            os.path.join(here, "pose_landmarker.task"),
        ]
        for path in candidates:
            if os.path.exists(path):
                return path
        return candidates[0]

    def detect_frame(self, frame):
        """One uint8 RGB frame -> (xyz float32 [K, 3], visibility float32 [K]).

        xyz rows are NaN where no pose was found. Coordinates are MediaPipe's
        normalized image space: origin top-left, y down, and NOT clamped to
        [0, 1] -- occluded joints are extrapolated outside the frame (crop.py
        measured a hip at y = 1.94 on real footage).

        `visibility` is the model's own confidence per landmark. landmarks_for_clip
        has always discarded it; the overlay needs it to avoid drawing bones to
        joints the model is guessing at.
        """
        mp = self._mp
        xyz = np.full((len(UPPER_BODY), 3), np.nan, dtype=np.float32)
        vis = np.zeros(len(UPPER_BODY), dtype=np.float32)

        image = mp.Image(image_format=mp.ImageFormat.SRGB,
                         data=np.ascontiguousarray(frame))
        result = self.detector.detect(image)
        if not result.pose_landmarks:
            return xyz, vis
        landmarks = result.pose_landmarks[0]
        if len(landmarks) <= max(UPPER_BODY):
            return xyz, vis
        for j, idx in enumerate(UPPER_BODY):
            lm = landmarks[idx]
            xyz[j] = (lm.x, lm.y, lm.z)
            vis[j] = float(getattr(lm, "visibility", 1.0) or 0.0)
        return xyz, vis

    def landmarks_for_clip(self, clip):
        """uint8 RGB [T, H, W, 3] -> float32 [T, K, 3]; NaN rows where no pose.

        Contract unchanged: the pose cache .npz and every DTW consumer depend on
        this exact shape. It is now a stack of detect_frame calls -- IMAGE mode
        is stateless, so per-frame and per-clip results are identical, and a
        test locks that in.
        """
        if len(clip) == 0:
            return np.full((0, len(UPPER_BODY), 3), np.nan, dtype=np.float32)
        return np.stack([self.detect_frame(frame)[0] for frame in clip])

    def close(self):
        self.detector.close()


def landmarks_to_overlay(xyz, visibility=None, min_visibility=0.5, mirror=True):
    """Drawable points for the live preview, or None when there is no pose.

    Returns a list of [x, y] (rounded, still normalized) or None per landmark,
    ready to serialize. The whole return is None when nothing was detected, so
    the client clears its canvas instead of holding a stale skeleton.

    All of the geometry lives here, in Python, rather than in the page's
    JavaScript -- there is no JS test runner in this repo, so anything that can
    be wrong about coordinates is kept where pytest can reach it.

    mirror: the preview is a CSS-mirrored selfie view, but the JPEG posted to
    the server is the unmirrored source. Flipping x here, once, keeps the two
    from drifting; the client never mirrors.

    Coordinates are NOT clamped to [0, 1]. A canvas clips them naturally,
    whereas clamping would pin a flailing wrist to the frame edge and draw a
    bone that never existed.
    """
    xyz = np.asarray(xyz, dtype=np.float32)
    if xyz.size == 0 or np.isnan(xyz).all():
        return None

    if visibility is None:
        visibility = np.ones(len(xyz), dtype=np.float32)
    visibility = np.asarray(visibility, dtype=np.float32)

    points = []
    for i in range(len(xyz)):
        x, y = float(xyz[i][0]), float(xyz[i][1])
        if np.isnan(x) or np.isnan(y) or visibility[i] < float(min_visibility):
            points.append(None)
            continue
        if mirror:
            x = 1.0 - x
        points.append([round(x, 4), round(y, 4)])

    if all(p is None for p in points):
        return None
    return points


def normalize_pose(sequence):
    """Center on the torso and scale by shoulder width (spec 11.3.2).

    Removes global translation and apparent size, so a medium shot and a close-up
    of the same gesture normalize to the same skeleton. That is exactly the
    domain gap that sinks the appearance-based stream when reference clips are
    framed differently from the target footage.
    """
    sequence = np.asarray(sequence, dtype=np.float32)
    out = np.full_like(sequence, np.nan)

    for t in range(len(sequence)):
        frame = sequence[t]
        if np.isnan(frame).all():
            continue
        left_sh, right_sh = frame[L_SHOULDER], frame[R_SHOULDER]
        if np.isnan(left_sh).any() or np.isnan(right_sh).any():
            continue

        # Torso origin: shoulder midpoint, falling back from the hip midpoint
        # when hips are out of frame (common for a seated desk shot).
        shoulder_mid = (left_sh + right_sh) / 2.0
        hips = frame[[L_HIP, R_HIP]]
        origin = shoulder_mid if np.isnan(hips).any() else (shoulder_mid + hips.mean(axis=0)) / 2.0

        width = float(np.linalg.norm(left_sh[:2] - right_sh[:2]))
        if width < 1e-4:
            continue
        out[t] = (frame - origin) / width

    return out


# Ear / wrist indices in the UPPER_BODY-trimmed array (MediaPipe 0..24).
L_EAR, R_EAR = 7, 8
L_WRIST, R_WRIST = 15, 16


def wrist_near_ear(sequence, threshold=0.28, min_frames_ratio=0.25):
    """True if a wrist is near an ear for enough frames (atmos-style gate).

    Operates on shoulder-normalized pose (`normalize_pose` output). Used as a
    CONFIRMATION gate for ear_cover opens so face-near appearance matches
    without a hand at the head cannot fire -- without turning on DTW pose
    fusion, which measured harmful on held-out eval3.
    """
    sequence = np.asarray(sequence, dtype=np.float32)
    if sequence.size == 0:
        return False
    hits = 0
    usable = 0
    pairs = ((L_WRIST, L_EAR), (L_WRIST, R_EAR), (R_WRIST, L_EAR), (R_WRIST, R_EAR))
    for frame in sequence:
        if np.isnan(frame).all():
            continue
        usable += 1
        for wi, ei in pairs:
            w, e = frame[wi], frame[ei]
            if np.isnan(w).any() or np.isnan(e).any():
                continue
            if float(np.linalg.norm(w[:2] - e[:2])) < threshold:
                hits += 1
                break
    if usable == 0:
        return False
    return (hits / usable) >= float(min_frames_ratio)


def classes_needing_wrist_gate(cfg):
    """Class names that must pass wrist_near_ear before opening."""
    names = []
    for name in getattr(cfg, "class_names", []) or []:
        if bool(cfg.class_cfg(name).get("require_wrist_near_ear", False)):
            names.append(name)
    return names


def _frame_distance(a, b):
    """Mean euclidean distance over landmarks present in both frames."""
    valid = ~(np.isnan(a).any(axis=-1) | np.isnan(b).any(axis=-1))
    if not valid.any():
        return np.inf
    return float(np.linalg.norm(a[valid] - b[valid], axis=-1).mean())


def dtw_distance(query, template, band_ratio=0.5):
    """Length-normalized DTW between two normalized keypoint sequences.

    Sakoe-Chiba band keeps the alignment from degenerating (one frame matching
    the whole template) and bounds the cost. Returns inf when either sequence
    holds no usable pose.
    """
    query = np.asarray(query, dtype=np.float32)
    template = np.asarray(template, dtype=np.float32)
    n, m = len(query), len(template)
    if n == 0 or m == 0:
        return np.inf
    if np.isnan(query).all() or np.isnan(template).all():
        return np.inf

    band = max(int(round(max(n, m) * band_ratio)), abs(n - m) + 1)
    cost = np.full((n + 1, m + 1), np.inf, dtype=np.float64)
    cost[0, 0] = 0.0

    for i in range(1, n + 1):
        lo = max(1, i - band)
        hi = min(m, i + band)
        for j in range(lo, hi + 1):
            d = _frame_distance(query[i - 1], template[j - 1])
            if not np.isfinite(d):
                continue
            cost[i, j] = d + min(cost[i - 1, j], cost[i, j - 1], cost[i - 1, j - 1])

    total = cost[n, m]
    if not np.isfinite(total):
        return np.inf
    return float(total / (n + m))          # normalize by path length


def similarity(query, templates, temperature=0.35):
    """Best template match as a similarity in (0, 1]: exp(-DTW / temperature)."""
    best = np.inf
    for template in templates:
        best = min(best, dtw_distance(query, template))
    if not np.isfinite(best):
        return 0.0
    return float(np.exp(-best / max(temperature, 1e-6)))


# -- caching ---------------------------------------------------------------


def cache_path(cfg, video_id):
    return cfg.path("cache", "pose", "%s.npz" % video_id)


def load_pose_cache(cfg, video_id):
    path = cache_path(cfg, video_id)
    if not os.path.exists(path):
        return None
    with np.load(path, allow_pickle=False) as data:
        if (
            float(data["fps"]) != float(cfg["working_fps"])
            or float(data["chunk_sec"]) != float(cfg["chunk_sec"])
        ):
            return None
        out = {"sequences": data["sequences"].astype(np.float32), "starts": data["starts"]}
        if "boxes" in data.files:
            out["boxes"] = data["boxes"].astype(np.float32)
        return out


def encode_video_pose(cfg, video_path, video_id, force=False, progress=True):
    """Per-chunk normalized keypoint sequences, cached alongside the V-JEPA ones."""
    from src.chunker import iter_chunks

    if not force:
        cached = load_pose_cache(cfg, video_id)
        if cached is not None:
            return cached

    extractor = PoseExtractor(
        model_path=cfg.get("pose", {}).get("model_path") or None,
        min_confidence=cfg.get("pose", {}).get("min_confidence", 0.5),
    )
    sequences, starts = [], []
    iterator = iter_chunks(video_path, cfg["working_fps"], cfg["chunk_sec"],
                           backend=cfg.get("video_backend"))
    if progress:
        try:
            from tqdm import tqdm

            iterator = tqdm(iterator, desc="pose %s" % video_id, unit="chunk")
        except ImportError:
            pass

    from src.crop import box_from_landmarks, smooth_boxes

    crop_cfg = cfg.get("crop", {})
    boxes = []
    try:
        for start_sec, frames in iterator:
            raw = extractor.landmarks_for_clip(frames)
            # Box from RAW image-space landmarks -- normalize_pose throws away
            # the image coordinates the crop needs.
            boxes.append(box_from_landmarks(
                raw,
                padding=float(crop_cfg.get("padding", 0.35)),
                min_size=float(crop_cfg.get("min_size", 0.25)),
                aspect=float(frames.shape[2]) / float(frames.shape[1]),
            ))
            sequences.append(normalize_pose(raw))
            starts.append(start_sec)
    finally:
        extractor.close()

    boxes = smooth_boxes(boxes, int(crop_cfg.get("smooth_window", 5)))

    if not sequences:
        raise RuntimeError("no chunks produced for pose from %s" % video_path)

    result = {
        "sequences": np.stack(sequences).astype(np.float32),
        "starts": np.asarray(starts, dtype=np.float32),
        "boxes": np.asarray(boxes, dtype=np.float32),
    }
    out = cache_path(cfg, video_id)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(
        out,
        sequences=result["sequences"],
        starts=result["starts"],
        boxes=result["boxes"],
        fps=np.float32(cfg["working_fps"]),
        chunk_sec=np.float32(cfg["chunk_sec"]),
    )
    return result


def reference_box(cfg, clip_path):
    """One crop box for a whole reference clip.

    One box, not per-frame: the reference must be cropped by the same rule as a
    target chunk, and a target chunk gets a single box spanning its duration.
    """
    from src.chunker import iter_frames
    from src.crop import FULL_FRAME, box_from_landmarks

    crop_cfg = cfg.get("crop", {})
    frames = np.stack(list(iter_frames(clip_path, cfg["working_fps"], cfg.get("video_backend"))))
    extractor = PoseExtractor(
        model_path=cfg.get("pose", {}).get("model_path") or None,
        min_confidence=cfg.get("pose", {}).get("min_confidence", 0.5),
    )
    try:
        raw = extractor.landmarks_for_clip(frames)
    finally:
        extractor.close()
    if np.isnan(raw).all():
        return FULL_FRAME
    return box_from_landmarks(
        raw,
        padding=float(crop_cfg.get("padding", 0.35)),
        min_size=float(crop_cfg.get("min_size", 0.25)),
        aspect=float(frames.shape[2]) / float(frames.shape[1]),
    )


# -- templates -------------------------------------------------------------


def template_bank_path(cfg):
    return cfg.path("cache", "pose_templates.npz")


def build_pose_templates(cfg, references_dir=None, verbose=True):
    """One normalized keypoint template per reference clip, per class."""
    from src.chunker import iter_frames

    references_dir = references_dir or cfg.path("data", "references")
    extractor = PoseExtractor(
        model_path=cfg.get("pose", {}).get("model_path") or None,
        min_confidence=cfg.get("pose", {}).get("min_confidence", 0.5),
    )
    templates = {}
    try:
        for class_name in cfg.class_names:
            class_dir = os.path.join(references_dir, class_name)
            clips = sorted(
                os.path.join(class_dir, f)
                for f in os.listdir(class_dir)
                if not f.startswith(".")
                and os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".avi", ".mkv")
            )
            per_class = []
            for clip_path in clips:
                frames = np.stack(list(iter_frames(clip_path, cfg["working_fps"],
                                                   cfg.get("video_backend"))))
                sequence = normalize_pose(extractor.landmarks_for_clip(frames))
                usable = int((~np.isnan(sequence).all(axis=(1, 2))).sum())
                if usable == 0:
                    print("  WARNING: no pose detected in %s" % os.path.basename(clip_path))
                    continue
                per_class.append(sequence)
                if verbose:
                    print("  %-16s %-22s %d/%d frames with a pose"
                          % (class_name, os.path.basename(clip_path), usable, len(sequence)))
            if not per_class:
                raise SystemExit("no usable pose template for class %r" % class_name)
            templates[class_name] = per_class
    finally:
        extractor.close()
    return templates


def save_pose_templates(cfg, templates):
    """Templates vary in length, so each is stored under its own key."""
    payload = {}
    index = []
    for class_name, sequences in templates.items():
        for i, sequence in enumerate(sequences):
            key = "%s::%d" % (class_name, i)
            payload[key] = sequence
            index.append(key)
    payload["__index__"] = np.asarray(index)
    out = template_bank_path(cfg)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, **payload)
    return out


def load_pose_templates(cfg):
    path = template_bank_path(cfg)
    if not os.path.exists(path):
        return None
    templates = {}
    with np.load(path, allow_pickle=False) as data:
        for key in data["__index__"]:
            class_name = str(key).split("::")[0]
            templates.setdefault(class_name, []).append(data[str(key)].astype(np.float32))
    return templates


def score_chunks(sequences, templates, class_names, temperature=0.35):
    """Per-chunk pose similarity for every class -> [n_class, n_chunks]."""
    grid = np.zeros((len(class_names), len(sequences)), dtype=np.float32)
    for ci, name in enumerate(class_names):
        class_templates = templates.get(name, [])
        if not class_templates:
            continue
        for t, sequence in enumerate(sequences):
            grid[ci, t] = similarity(sequence, class_templates, temperature)
    return grid

"""Hand landmark stream: finger configuration (spec 11.3, third stream).

Why a third stream. Measured on real footage, ear_cover scored +1.70 sigma from
pose while hair_twirling scored -0.18 -- barely above chance. The reason is
anatomical, not statistical: MediaPipe POSE carries no finger landmarks. It has
wrist, elbow, shoulder and three crude hand approximations. Ear-cover is defined
by whole-arm geometry, which pose measures exactly. Hair-twirling is defined by
fingers moving in hair while the arm stays roughly still -- pose literally
cannot see the distinguishing detail, and V-JEPA averages it away.

HandLandmarker gives 21 landmarks per hand. Normalization here is deliberately
hand-LOCAL (wrist-centred, scaled by hand span), so this stream encodes finger
configuration and nothing else. Where the hand is relative to the head is
already the pose stream's job; keeping them disjoint is what makes the per-class
fusion weights interpretable.

Frozen model, no training. A new action is still just a new reference clip.
"""

import os

import numpy as np

from src.pose import dtw_distance, similarity  # generic over [T, K, 3]

WRIST = 0
MIDDLE_MCP = 9          # knuckle of the middle finger: a stable size reference
N_HAND_LANDMARKS = 21


class HandExtractor:
    """MediaPipe HandLandmarker, frozen, IMAGE mode, one hand."""

    def __init__(self, model_path=None, min_confidence=0.4, num_hands=1):
        import mediapipe as mp
        from mediapipe.tasks import python
        from mediapipe.tasks.python import vision

        model_path = model_path or self._default_model_path()
        if not os.path.exists(model_path):
            raise FileNotFoundError(
                "hand model not found at %s -- download hand_landmarker.task from\n"
                "storage.googleapis.com/mediapipe-models/hand_landmarker/" % model_path
            )
        options = vision.HandLandmarkerOptions(
            base_options=python.BaseOptions(
                model_asset_path=model_path,
                delegate=python.BaseOptions.Delegate.CPU,
            ),
            running_mode=vision.RunningMode.IMAGE,
            num_hands=num_hands,
            min_hand_detection_confidence=min_confidence,
            min_hand_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self.detector = vision.HandLandmarker.create_from_options(options)
        self._mp = mp

    @staticmethod
    def _default_model_path():
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(here, "hand_landmarker.task")

    def landmarks_for_clip(self, clip):
        """uint8 RGB [T, H, W, 3] -> float32 [T, 21, 3]; NaN where no hand."""
        mp = self._mp
        out = np.full((len(clip), N_HAND_LANDMARKS, 3), np.nan, dtype=np.float32)
        for i, frame in enumerate(clip):
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(frame))
            result = self.detector.detect(image)
            if not result.hand_landmarks:
                continue
            hand = result.hand_landmarks[0]
            for j, lm in enumerate(hand[:N_HAND_LANDMARKS]):
                out[i, j] = (lm.x, lm.y, lm.z)
        return out

    def close(self):
        self.detector.close()


def normalize_hand(sequence):
    """Wrist-centred, scaled by hand span. Encodes finger shape only.

    Deliberately discards where the hand is in the frame: position relative to
    the body is the pose stream's contribution, and overlapping the two would
    make their fusion weights meaningless.
    """
    sequence = np.asarray(sequence, dtype=np.float32)
    out = np.full_like(sequence, np.nan)
    for t in range(len(sequence)):
        frame = sequence[t]
        if np.isnan(frame).all():
            continue
        wrist, knuckle = frame[WRIST], frame[MIDDLE_MCP]
        if np.isnan(wrist).any() or np.isnan(knuckle).any():
            continue
        span = float(np.linalg.norm(knuckle[:2] - wrist[:2]))
        if span < 1e-4:
            continue
        out[t] = (frame - wrist) / span
    return out


# -- caching ---------------------------------------------------------------


def cache_path(cfg, video_id):
    return cfg.path("cache", "hands", "%s.npz" % video_id)


def load_hand_cache(cfg, video_id):
    path = cache_path(cfg, video_id)
    if not os.path.exists(path):
        return None
    with np.load(path, allow_pickle=False) as data:
        if (float(data["fps"]) != float(cfg["working_fps"])
                or float(data["chunk_sec"]) != float(cfg["chunk_sec"])):
            return None
        return {"sequences": data["sequences"].astype(np.float32), "starts": data["starts"]}


def encode_video_hands(cfg, video_path, video_id, force=False, progress=True):
    from src.chunker import iter_chunks

    if not force:
        cached = load_hand_cache(cfg, video_id)
        if cached is not None:
            return cached

    hands_cfg = cfg.get("hands", {})
    extractor = HandExtractor(
        model_path=hands_cfg.get("model_path") or None,
        min_confidence=hands_cfg.get("min_confidence", 0.4),
    )
    sequences, starts = [], []
    iterator = iter_chunks(video_path, cfg["working_fps"], cfg["chunk_sec"],
                           backend=cfg.get("video_backend"))
    if progress:
        try:
            from tqdm import tqdm
            iterator = tqdm(iterator, desc="hands %s" % video_id, unit="chunk")
        except ImportError:
            pass
    try:
        for start_sec, frames in iterator:
            sequences.append(normalize_hand(extractor.landmarks_for_clip(frames)))
            starts.append(start_sec)
    finally:
        extractor.close()

    if not sequences:
        raise RuntimeError("no chunks produced for hands from %s" % video_path)

    result = {"sequences": np.stack(sequences).astype(np.float32),
              "starts": np.asarray(starts, dtype=np.float32)}
    out = cache_path(cfg, video_id)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, sequences=result["sequences"], starts=result["starts"],
             fps=np.float32(cfg["working_fps"]), chunk_sec=np.float32(cfg["chunk_sec"]))
    return result


# -- templates -------------------------------------------------------------


def template_bank_path(cfg):
    return cfg.path("cache", "hand_templates.npz")


def build_hand_templates(cfg, references_dir=None, verbose=True):
    from src.chunker import iter_frames

    references_dir = references_dir or cfg.path("data", "references")
    hands_cfg = cfg.get("hands", {})
    extractor = HandExtractor(
        model_path=hands_cfg.get("model_path") or None,
        min_confidence=hands_cfg.get("min_confidence", 0.4),
    )
    templates = {}
    try:
        for class_name in cfg.class_names:
            class_dir = os.path.join(references_dir, class_name)
            clips = sorted(
                os.path.join(class_dir, f) for f in os.listdir(class_dir)
                if not f.startswith(".")
                and os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".avi", ".mkv")
            )
            per_class = []
            for clip_path in clips:
                frames = np.stack(list(iter_frames(clip_path, cfg["working_fps"],
                                                   cfg.get("video_backend"))))
                seq = normalize_hand(extractor.landmarks_for_clip(frames))
                usable = int((~np.isnan(seq).all(axis=(1, 2))).sum())
                if verbose:
                    print("  %-16s %-22s %d/%d frames with a hand"
                          % (class_name, os.path.basename(clip_path), usable, len(seq)))
                if usable == 0:
                    continue
                per_class.append(seq)
            if per_class:
                templates[class_name] = per_class
            elif verbose:
                print("  WARNING: no hand detected in any %s reference" % class_name)
    finally:
        extractor.close()
    return templates


def save_hand_templates(cfg, templates):
    payload, index = {}, []
    for class_name, sequences in templates.items():
        for i, seq in enumerate(sequences):
            key = "%s::%d" % (class_name, i)
            payload[key] = seq
            index.append(key)
    payload["__index__"] = np.asarray(index)
    out = template_bank_path(cfg)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez(out, **payload)
    return out


def load_hand_templates(cfg):
    path = template_bank_path(cfg)
    if not os.path.exists(path):
        return None
    templates = {}
    with np.load(path, allow_pickle=False) as data:
        for key in data["__index__"]:
            templates.setdefault(str(key).split("::")[0], []).append(data[str(key)].astype(np.float32))
    return templates


def score_chunks(sequences, templates, class_names, temperature=0.35):
    grid = np.zeros((len(class_names), len(sequences)), dtype=np.float32)
    for ci, name in enumerate(class_names):
        class_templates = templates.get(name, [])
        if not class_templates:
            continue
        for t, seq in enumerate(sequences):
            grid[ci, t] = similarity(seq, class_templates, temperature)
    return grid

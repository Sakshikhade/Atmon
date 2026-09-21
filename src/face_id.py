"""Face identity gate via EdgeFace-XS-GAMMA (Active Subject).

License: EdgeFace weights and architecture are CC BY-NC-SA 4.0 (non-commercial).
See https://huggingface.co/Idiap/EdgeFace-XS-GAMMA

Identity is a SESSION gate, not an action stream: when enabled, Live/offline
opens are suppressed unless the camera face matches the selected subject's
gallery (harvested from that subject's action reference clips).
"""

from __future__ import annotations

import json
import os
import time

import numpy as np

from src.subjects import (
    face_gallery_path,
    face_meta_path,
    subject_face_dir,
    subject_references_dir,
)

EMBED_DIM = 512


def identity_enabled(cfg):
    return bool(cfg.get("identity", {}).get("enabled", False))


def match_threshold(cfg):
    return float(cfg.get("identity", {}).get("match_threshold", 0.40))


def l2_normalize(vec):
    vec = np.asarray(vec, dtype=np.float32)
    if vec.ndim == 1:
        n = float(np.linalg.norm(vec))
        if n < 1e-12:
            return vec
        return (vec / n).astype(np.float32)
    norms = np.linalg.norm(vec, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-12)
    return (vec / norms).astype(np.float32)


def cosine_match(embedding, gallery, threshold):
    """True if max cosine(embedding, gallery row) >= threshold."""
    if gallery is None:
        return False
    gallery = np.asarray(gallery, dtype=np.float32)
    if gallery.size == 0 or gallery.ndim != 2:
        return False
    emb = l2_normalize(embedding).reshape(1, -1)
    g = l2_normalize(gallery)
    scores = (emb @ g.T).ravel()
    return bool(float(scores.max()) >= float(threshold))


def max_cosine(embedding, gallery):
    if gallery is None or np.asarray(gallery).size == 0:
        return float("-inf")
    emb = l2_normalize(embedding).reshape(1, -1)
    g = l2_normalize(np.asarray(gallery, dtype=np.float32))
    return float((emb @ g.T).ravel().max())


def load_gallery(cfg, subject_id):
    path = face_gallery_path(cfg, subject_id)
    if not os.path.isfile(path):
        return None
    arr = np.load(path)
    if arr.ndim != 2 or arr.shape[1] != EMBED_DIM or arr.shape[0] == 0:
        return None
    return arr.astype(np.float32)


def save_gallery(cfg, subject_id, embeddings, sources=None):
    os.makedirs(subject_face_dir(cfg, subject_id), exist_ok=True)
    embeddings = l2_normalize(np.asarray(embeddings, dtype=np.float32))
    path = face_gallery_path(cfg, subject_id)
    np.save(path, embeddings)
    meta = {
        "subject_id": subject_id,
        "n_embeddings": int(embeddings.shape[0]),
        "embed_dim": int(embeddings.shape[1]),
        "model_id": "edgeface_xs_gamma_06",
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "sources": sources or [],
    }
    with open(face_meta_path(cfg, subject_id), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2, sort_keys=True)
        fh.write("\n")
    return path


def _crop_face_bgr_or_rgb(frame_rgb, detection, pad=0.25):
    """Square crop around a MediaPipe relative bounding box -> uint8 RGB."""
    import cv2

    h, w = frame_rgb.shape[:2]
    bbox = detection.location_data.relative_bounding_box
    x0 = bbox.xmin * w
    y0 = bbox.ymin * h
    bw = bbox.width * w
    bh = bbox.height * h
    cx, cy = x0 + bw / 2.0, y0 + bh / 2.0
    side = max(bw, bh) * (1.0 + 2.0 * pad)
    x1 = int(max(0, cx - side / 2.0))
    y1 = int(max(0, cy - side / 2.0))
    x2 = int(min(w, cx + side / 2.0))
    y2 = int(min(h, cy + side / 2.0))
    if x2 - x1 < 8 or y2 - y1 < 8:
        return None
    crop = frame_rgb[y1:y2, x1:x2]
    return cv2.resize(crop, (112, 112), interpolation=cv2.INTER_LINEAR)


class FaceIdEncoder:
    """EdgeFace embedder + MediaPipe Face Detection for 112×112 crops.

    Pass ``model=`` / ``detector=`` in tests to inject fakes.
    """

    def __init__(self, cfg, model=None, detector=None, device=None):
        self.cfg = cfg
        self.threshold = match_threshold(cfg)
        self.min_conf = float(
            cfg.get("identity", {}).get("min_detection_confidence", 0.5)
        )
        self._model = model
        self._detector = detector
        self._device = device
        self._mp_ctx = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        import torch

        from src.edgeface import load_edgeface_xs_gamma_06

        path = self.cfg.get("identity", {}).get("model_path") or "models/edgeface_xs_gamma_06.pt"
        if not os.path.isabs(path):
            path = self.cfg.path(path)
        if not os.path.isfile(path):
            raise FileNotFoundError(
                "EdgeFace checkpoint not found at %s -- place "
                "edgeface_xs_gamma_06.pt there (CC BY-NC-SA 4.0)" % path
            )
        if self._device is None:
            if torch.cuda.is_available():
                self._device = "cuda"
            elif getattr(torch.backends, "mps", None) and torch.backends.mps.is_available():
                self._device = "mps"
            else:
                self._device = "cpu"
        self._model = load_edgeface_xs_gamma_06(path, map_location="cpu")
        self._model.to(self._device)
        self._model.eval()
        return self._model

    def _ensure_detector(self):
        if self._detector is not None:
            return self._detector
        import mediapipe as mp

        self._mp_ctx = mp.solutions.face_detection.FaceDetection(
            model_selection=0, min_detection_confidence=self.min_conf
        )
        self._detector = self._mp_ctx
        return self._detector

    def close(self):
        if self._mp_ctx is not None:
            self._mp_ctx.close()
            self._mp_ctx = None
            self._detector = None

    def detect_crop(self, frame_rgb):
        """Largest face crop as uint8 RGB [112,112,3], or None."""
        det = self._ensure_detector()
        result = det.process(frame_rgb)
        if not result.detections:
            return None
        # Highest score first
        best = max(
            result.detections,
            key=lambda d: float(d.score[0]) if d.score else 0.0,
        )
        return _crop_face_bgr_or_rgb(frame_rgb, best)

    def embed_crop(self, crop_rgb):
        """L2-normalized 512-D embedding from a 112×112 RGB crop."""
        import torch

        model = self._ensure_model()
        x = crop_rgb.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        t = torch.from_numpy(x).permute(2, 0, 1).unsqueeze(0)
        t = t.to(self._device)
        with torch.no_grad():
            emb = model(t).detach().cpu().numpy().reshape(-1)
        return l2_normalize(emb)

    def embed_frame(self, frame_rgb):
        """Detect + embed; returns (embedding, crop) or (None, None)."""
        crop = self.detect_crop(frame_rgb)
        if crop is None:
            return None, None
        return self.embed_crop(crop), crop

    def matches_gallery(self, frame_rgb, gallery):
        emb, _ = self.embed_frame(frame_rgb)
        if emb is None:
            return False, None
        return cosine_match(emb, gallery, self.threshold), emb


def harvest_subject_faces(cfg, subject_id, encoder=None, verbose=True):
    """Sample faces from subject reference clips into face/embeddings.npy.

    Soft-fails (returns 0) when no faces are found so bank rebuild can continue.
    """
    from src.chunker import iter_frames

    refs = subject_references_dir(cfg, subject_id)
    if not os.path.isdir(refs):
        if verbose:
            print("  face harvest: no references dir for %s" % subject_id)
        return 0

    n_per = int(cfg.get("identity", {}).get("harvest_frames_per_clip", 8))
    working_fps = float(cfg["working_fps"])
    own_encoder = encoder is None
    encoder = encoder or FaceIdEncoder(cfg)
    embeddings = []
    sources = []
    try:
        for class_name in sorted(os.listdir(refs)):
            class_dir = os.path.join(refs, class_name)
            if not os.path.isdir(class_dir):
                continue
            clips = sorted(
                f for f in os.listdir(class_dir)
                if not f.startswith(".")
                and os.path.splitext(f)[1].lower() in (".mp4", ".mov", ".avi", ".mkv")
            )
            for clip_name in clips:
                clip_path = os.path.join(class_dir, clip_name)
                frames = list(iter_frames(clip_path, working_fps, cfg.get("video_backend")))
                if not frames:
                    continue
                if len(frames) <= n_per:
                    idxs = list(range(len(frames)))
                else:
                    idxs = np.linspace(0, len(frames) - 1, n_per).astype(int).tolist()
                for i in idxs:
                    emb, _ = encoder.embed_frame(frames[i])
                    if emb is None:
                        continue
                    embeddings.append(emb)
                    sources.append({
                        "clip": os.path.join(class_name, clip_name),
                        "frame_index": int(i),
                    })
    finally:
        if own_encoder:
            encoder.close()

    if not embeddings:
        if verbose:
            print("  face harvest: no faces found for subject %s -- gallery empty"
                  % subject_id)
        return 0

    save_gallery(cfg, subject_id, np.stack(embeddings, axis=0), sources=sources)
    if verbose:
        print("  face harvest: %d embeddings for subject %s"
              % (len(embeddings), subject_id))
    return len(embeddings)


def face_match_per_chunk(cfg, video_path, subject_id, starts, chunk_sec, encoder=None):
    """Bool array aligned to chunk starts: True when mid-chunk face matches gallery."""
    from src.chunker import iter_frames

    gallery = load_gallery(cfg, subject_id)
    if gallery is None:
        return np.zeros(len(starts), dtype=bool)

    working_fps = float(cfg["working_fps"])
    frames = list(iter_frames(video_path, working_fps, cfg.get("video_backend")))
    if not frames:
        return np.zeros(len(starts), dtype=bool)

    own = encoder is None
    encoder = encoder or FaceIdEncoder(cfg)
    out = np.zeros(len(starts), dtype=bool)
    try:
        for t, start in enumerate(starts):
            mid = float(start) + 0.5 * float(chunk_sec)
            idx = int(round(mid * working_fps))
            idx = max(0, min(len(frames) - 1, idx))
            ok, _ = encoder.matches_gallery(frames[idx], gallery)
            out[t] = bool(ok)
    finally:
        if own:
            encoder.close()
    return out

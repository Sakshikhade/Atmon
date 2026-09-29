"""Frozen V-JEPA 2 wrapper (spec 2, 5.3).

THE SINGLE ENCODING PATH. Reference clips, offline video chunks, and live camera
chunks all reach the model through Encoder.encode_clips() and nowhere else. That
is deliberate: spec 14.1 and 14.9 name a divergence between these paths as the
most common silent failure in the whole system, so there is exactly one function
that resamples, preprocesses, pools, and normalizes, and no way around it.

Any encoder exposing encode_clips(list_of_uint8_RGB_clips) -> float32 [B, D],
plus .model_id and .frames_per_clip, is a drop-in (see tests/stub_encoder.py).
"""

import numpy as np


def select_device(preference="auto"):
    """cuda -> mps -> cpu. The spec targets CUDA; this keeps it runnable elsewhere."""
    import torch

    if preference and preference != "auto":
        return torch.device(preference)
    if torch.cuda.is_available():
        return torch.device("cuda")
    if getattr(torch.backends, "mps", None) is not None and torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def select_dtype(device, preference="auto"):
    """Autocast dtype for the device, or None to run in fp32.

    MPS autocast for fp16 is unreliable across torch versions and silently
    degrades similarity, so Apple Silicon runs fp32.
    """
    import torch

    named = {"bf16": torch.bfloat16, "fp16": torch.float16, "fp32": None}
    if preference and preference != "auto":
        if preference not in named:
            raise ValueError("dtype must be one of auto/bf16/fp16/fp32, got %r" % preference)
        return named[preference]

    if device.type == "cuda":
        if torch.cuda.is_bf16_supported():
            return torch.bfloat16
        return torch.float16
    return None


def resample_frame_indices(n_available, n_target):
    """Map n_available frames onto exactly n_target slots, evenly.

    Upsamples by repeating frames when a chunk is shorter than the model's clip
    length, downsamples by dropping when longer. Used by every path, so the
    reference and target frame grids cannot diverge.
    """
    if n_available <= 0:
        raise ValueError("cannot resample an empty clip")
    if n_available == 1:
        return np.zeros(n_target, dtype=np.int64)
    idx = np.linspace(0, n_available - 1, num=n_target)
    return np.rint(idx).astype(np.int64)


def fit_clip_length(clip, frames_per_clip):
    """Resample one uint8 clip [T, H, W, 3] to exactly frames_per_clip frames."""
    clip = np.asarray(clip)
    if clip.ndim != 4 or clip.shape[-1] != 3:
        raise ValueError("clip must be [T, H, W, 3] uint8 RGB, got shape %r" % (clip.shape,))
    return clip[resample_frame_indices(clip.shape[0], frames_per_clip)]


def l2_normalize(x, axis=-1, eps=1e-12):
    """Spec 14.2: normalize before every cosine similarity, without exception."""
    x = np.asarray(x, dtype=np.float32)
    norm = np.linalg.norm(x, axis=axis, keepdims=True)
    return x / np.maximum(norm, eps)


class Encoder:
    """V-JEPA 2, frozen, eval mode, no_grad."""

    def __init__(self, model_id, device="auto", dtype="auto", frames_per_clip=64, batch_size=4):
        import torch
        from transformers import AutoModel

        self.model_id = model_id
        self.frames_per_clip = frames_per_clip
        self.batch_size = batch_size
        self.device = select_device(device)
        self.autocast_dtype = select_dtype(self.device, dtype)
        self._torch = torch

        self.processor = self._load_processor(model_id)
        self.model = AutoModel.from_pretrained(model_id)
        self.model.eval().to(self.device)
        for param in self.model.parameters():
            param.requires_grad_(False)

        self._dim = None

    @staticmethod
    def _load_processor(model_id):
        """AutoVideoProcessor is the V-JEPA 2 path; older transformers lack it."""
        try:
            from transformers import AutoVideoProcessor

            return AutoVideoProcessor.from_pretrained(model_id)
        except (ImportError, ValueError):
            from transformers import AutoImageProcessor

            return AutoImageProcessor.from_pretrained(model_id)

    @property
    def dim(self):
        if self._dim is None:
            raise RuntimeError("encoder dimension is unknown until the first encode_clips() call")
        return self._dim

    def _preprocess(self, clips):
        """uint8 RGB clips -> the tensor the model expects, on the right device."""
        fitted = [fit_clip_length(c, self.frames_per_clip) for c in clips]
        inputs = self.processor(fitted, return_tensors="pt")
        key = "pixel_values_videos" if "pixel_values_videos" in inputs else "pixel_values"
        return {key: inputs[key].to(self.device)}

    def _forward(self, inputs):
        """Token features [B, N, D] from whichever API this transformers exposes."""
        if hasattr(self.model, "get_vision_features"):
            return self.model.get_vision_features(**inputs)
        return self.model(**inputs).last_hidden_state

    def encode_clips(self, clips):
        """Encode clips to L2-normalized embeddings.

        clips: sequence of uint8 RGB arrays [T, H, W, 3]; T may vary per clip.
        returns: float32 [B, D], each row unit norm.
        """
        if len(clips) == 0:
            return np.zeros((0, self._dim or 0), dtype=np.float32)

        torch = self._torch
        out = []
        for start in range(0, len(clips), self.batch_size):
            batch = clips[start : start + self.batch_size]
            inputs = self._preprocess(batch)
            with torch.no_grad():
                if self.autocast_dtype is not None:
                    with torch.autocast(device_type=self.device.type, dtype=self.autocast_dtype):
                        tokens = self._forward(inputs)
                else:
                    tokens = self._forward(inputs)
                # Mean-pool over tokens to one vector per clip (spec 5.3).
                pooled = tokens.float().mean(dim=1)
            out.append(pooled.cpu().numpy())

        feats = l2_normalize(np.concatenate(out, axis=0))
        self._dim = feats.shape[1]
        return feats.astype(np.float32)


def build_encoder(cfg, batch_size=4):
    """The active appearance backbone (config `backbone`).

    Imported lazily to keep src.backbones -> src.encoder from being circular.
    """
    from src.backbones import build

    return build(cfg.get("backbone", "vjepa"), cfg, batch_size=batch_size)

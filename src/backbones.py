"""Interchangeable appearance backbones.

X-CLIP is the default. Measured held out on a 9.3-minute video in an unseen
room, its appearance stream alone reached mAP@0.5 0.750 with zero false alarms,
where V-JEPA 2 reached 0.000 -- self-supervised video features encode the room
(they read +1 sigma in one and -1 in another), while a video-LANGUAGE encoder is
pushed to discard what a caption would not mention.

Every backbone exposes exactly the interface src/encoder.Encoder does --
encode_clips(list of uint8 RGB [T, H, W, 3]) -> float32 [B, D], L2-normalized --
so nothing downstream has to know which one is running. That is the whole reason
a backbone swap is a Phase B rather than a rewrite.

All of them are fed IDENTICAL chunks with the IDENTICAL crop, so the backbone is
the only variable (spec 14.1 applies across experiments too, not just within
one).
"""

import numpy as np

from src.encoder import fit_clip_length, l2_normalize, select_device, select_dtype


def pooled(output):
    """Extract the embedding tensor from a transformers output.

    transformers 5.x returns BaseModelOutputWithPooling from get_video_features
    and get_image_features rather than a bare tensor. `pooler_output` is the
    projected embedding -- for X-CLIP it is [B, projection_dim] AFTER the
    multiframe integration transformer, so temporal structure is preserved;
    last_hidden_state would be per-frame and pre-projection.
    """
    if hasattr(output, "pooler_output") and output.pooler_output is not None:
        return output.pooler_output
    if hasattr(output, "last_hidden_state"):
        return output.last_hidden_state.mean(dim=1)
    return output


class Backbone:
    name = "abstract"

    def encode_clips(self, clips):
        raise NotImplementedError


class VJepaBackbone(Backbone):
    """The shipped encoder, as the baseline to beat."""

    name = "vjepa"

    def __init__(self, cfg, batch_size=4, model_id=None, frames_per_clip=None):
        from src.encoder import Encoder

        model_id = model_id or cfg["model_id"]
        frames = int(frames_per_clip or cfg["frames_per_clip"])
        self.inner = Encoder(model_id, cfg["device"], cfg["dtype"], frames, batch_size)
        self.model_id = model_id
        self.frames_per_clip = frames
        self.device = self.inner.device

    def encode_clips(self, clips):
        return self.inner.encode_clips(clips)


class XClipBackbone(Backbone):
    """X-CLIP video encoder: video-text pretrained, with cross-frame attention.

    Uses get_video_features, i.e. the projection into the space text was aligned
    to -- that is where the appearance abstraction lives. Its own hidden states
    would be closer to raw appearance again.
    """

    name = "xclip"

    def __init__(self, cfg, model_id="microsoft/xclip-base-patch16", batch_size=2,
                 frames_per_clip=None):
        import torch
        from transformers import AutoProcessor, XCLIPModel

        self.model_id = model_id
        self.batch_size = batch_size
        self.device = select_device(cfg["device"])
        self.autocast_dtype = select_dtype(self.device, cfg["dtype"])
        self._torch = torch

        self.processor = AutoProcessor.from_pretrained(model_id)
        self.model = XCLIPModel.from_pretrained(model_id).eval().to(self.device)
        for p in self.model.parameters():
            p.requires_grad_(False)

        # X-CLIP is trained at a fixed clip length; feeding a different count
        # silently degrades it, so resample to whatever this checkpoint wants.
        self.frames_per_clip = int(getattr(self.model.config.vision_config, "num_frames", 8))

    def encode_clips(self, clips):
        torch = self._torch
        if not len(clips):
            return np.zeros((0, 0), dtype=np.float32)
        out = []
        for start in range(0, len(clips), self.batch_size):
            batch = [fit_clip_length(c, self.frames_per_clip) for c in clips[start:start + self.batch_size]]
            inputs = self.processor(videos=[list(c) for c in batch], return_tensors="pt")
            pixel_values = inputs["pixel_values"].to(self.device)
            with torch.no_grad():
                if self.autocast_dtype is not None:
                    with torch.autocast(device_type=self.device.type, dtype=self.autocast_dtype):
                        feats = pooled(self.model.get_video_features(pixel_values=pixel_values))
                else:
                    feats = pooled(self.model.get_video_features(pixel_values=pixel_values))
            out.append(feats.float().cpu().numpy())
        return l2_normalize(np.concatenate(out, axis=0)).astype(np.float32)


class SiglipFrameBackbone(Backbone):
    """SigLIP per frame, mean-pooled over the chunk.

    Image-text rather than video-text, so it has no motion modelling at all.
    Included deliberately as a control: if a purely per-frame text-aligned
    encoder matches or beats the video models, then what these behaviours need
    is appearance abstraction rather than temporal modelling -- which would say
    something useful about where the remaining headroom is.
    """

    name = "siglip"

    def __init__(self, cfg, model_id="google/siglip-base-patch16-224",
                 frames_per_clip=8, batch_size=16):
        frames_per_clip = int(frames_per_clip or 8)
        import torch
        from transformers import AutoImageProcessor, SiglipModel

        self.model_id = model_id
        self.frames_per_clip = int(frames_per_clip)
        self.batch_size = batch_size
        self.device = select_device(cfg["device"])
        self.autocast_dtype = select_dtype(self.device, cfg["dtype"])
        self._torch = torch

        self.processor = AutoImageProcessor.from_pretrained(model_id)
        self.model = SiglipModel.from_pretrained(model_id).eval().to(self.device)
        for p in self.model.parameters():
            p.requires_grad_(False)

    def encode_clips(self, clips):
        torch = self._torch
        if not len(clips):
            return np.zeros((0, 0), dtype=np.float32)
        out = []
        for clip in clips:
            frames = list(fit_clip_length(clip, self.frames_per_clip))
            vectors = []
            for i in range(0, len(frames), self.batch_size):
                inputs = self.processor(images=frames[i:i + self.batch_size], return_tensors="pt")
                pixel_values = inputs["pixel_values"].to(self.device)
                with torch.no_grad():
                    if self.autocast_dtype is not None:
                        with torch.autocast(device_type=self.device.type, dtype=self.autocast_dtype):
                            feats = pooled(self.model.get_image_features(pixel_values=pixel_values))
                    else:
                        feats = pooled(self.model.get_image_features(pixel_values=pixel_values))
                vectors.append(feats.float().cpu().numpy())
            # Normalize per frame BEFORE averaging, so one bright frame cannot
            # dominate the chunk by magnitude alone.
            frame_vectors = l2_normalize(np.concatenate(vectors, axis=0))
            out.append(frame_vectors.mean(axis=0))
        return l2_normalize(np.stack(out)).astype(np.float32)


REGISTRY = {
    "vjepa": VJepaBackbone,
    "xclip": XClipBackbone,
    "siglip": SiglipFrameBackbone,
}


def settings_for(cfg, name):
    """Per-backbone overrides from config.yaml's `backbones` block."""
    return dict(cfg.get("backbones", {}).get(name, {}) or {})


def build(name, cfg, **kwargs):
    """Instantiate a backbone by name, applying its config overrides."""
    if name not in REGISTRY:
        raise ValueError("unknown backbone %r; choose from %s" % (name, ", ".join(REGISTRY)))
    options = settings_for(cfg, name)
    options.update(kwargs)
    return REGISTRY[name](cfg, **options)

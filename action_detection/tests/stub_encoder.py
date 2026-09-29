"""A deterministic stand-in for V-JEPA 2 (numpy only).

Exposes the same surface as src.encoder.Encoder -- encode_clips, model_id,
frames_per_clip -- so every module downstream of the encoder can be tested
without torch, transformers, or 1.2 GB of weights. Swapping the real encoder
back in is a one-line change at the call site, which is the point.

The embedding is hand-built rather than learned: blob-centroid motion statistics
plus a coarse appearance histogram. Enough to separate "oscillating", "drifting"
and "static" synthetic clips, and nothing like a real video representation. Use
it to test PLUMBING -- pooling, grouping, thresholds, the event log -- never to
draw conclusions about detection quality.

Every motion feature here is a RATE or a SPREAD, never a cycle count, so that a
reference and a target of similar duration embed alike.

What this does NOT buy is invariance across ALL durations, and the reason
applies to the real encoder too. Everything is resampled to a fixed
`frames_per_clip` before encoding, so there is a boundary at
`frames_per_clip / working_fps` seconds: clips shorter than that are frame-
REPEATED to fill the grid, clips at or above it are DECIMATED to fit. Those two
regimes look materially different to any encoder. Measured here, a 2 Hz
oscillation at 16 frames/clip and 8 fps (boundary = 2.0 s):

    1 s clip ->  8 real frames, upsampled   -> cos 0.79 against a 2 s reference
    2 s clip -> 16 real frames, exact       -> cos 1.00
    3 s clip -> 24 real frames, decimated   -> cos 1.00

The practical consequence, which build_prototypes.py now warns about: a
reference clip shorter than `frames_per_clip / working_fps` gets padded with
repeats while every target chunk does not, and similarity degrades for reasons
that have nothing to do with the action.

(The 2 s/3 s clips both reading 1.00 is a stub artifact -- aliasing saturates
these hand-built features. A real encoder would still see the 3 s version as
slower motion.)
"""

import numpy as np

from src.encoder import fit_clip_length, l2_normalize

GRID = 3


def _centroids(clip):
    """Brightness-weighted centroid per frame, normalized to [0, 1]."""
    gray = clip.astype(np.float32).mean(axis=-1)          # [T, H, W]
    total = gray.sum(axis=(1, 2)) + 1e-6
    h, w = gray.shape[1], gray.shape[2]
    ys = np.arange(h, dtype=np.float32)[None, :, None]
    xs = np.arange(w, dtype=np.float32)[None, None, :]
    cy = (gray * ys).sum(axis=(1, 2)) / total / max(h - 1, 1)
    cx = (gray * xs).sum(axis=(1, 2)) / total / max(w - 1, 1)
    return np.stack([cx, cy], axis=1)                     # [T, 2]


def _motion(track):
    """Duration-invariant motion descriptor of a centroid track.

    speed     -- mean per-frame displacement; ~0 when static
    spread    -- std of position; high when moving at all
    extent    -- peak-to-peak range
    winding   -- path length / extent, weighted by speed. Separates oscillation
                 (retraces the same range many times, so >> 1) from drift
                 (crosses once, so ~1). Weighting by speed keeps centroid jitter
                 on a static clip from faking a high winding number.
    """
    steps = np.diff(track, axis=0)
    if len(steps) == 0:
        return np.zeros(8, dtype=np.float32)

    speed = np.abs(steps).mean(axis=0)                    # [2]
    spread = track.std(axis=0)                            # [2]
    extent = track.max(axis=0) - track.min(axis=0)        # [2]
    path = np.abs(steps).sum(axis=0)
    winding = (path / np.maximum(extent, 1e-3)) * speed   # [2]
    return np.concatenate([speed, spread, extent, winding]).astype(np.float32)


def _appearance(clip):
    """Mean intensity over a GRID x GRID tiling, averaged across time."""
    gray = clip.astype(np.float32).mean(axis=-1) / 255.0
    h, w = gray.shape[1], gray.shape[2]
    ys = np.linspace(0, h, GRID + 1).astype(int)
    xs = np.linspace(0, w, GRID + 1).astype(int)
    cells = [
        gray[:, ys[i] : ys[i + 1], xs[j] : xs[j + 1]].mean()
        for i in range(GRID)
        for j in range(GRID)
    ]
    return np.asarray(cells, dtype=np.float32)


class StubEncoder:
    model_id = "stub-encoder-v1"

    def __init__(self, frames_per_clip=16, motion_weight=8.0):
        self.frames_per_clip = frames_per_clip
        # Motion dominates appearance: these clips differ by movement, and an
        # unweighted concat lets the identical background swamp the signal.
        self.motion_weight = motion_weight
        self.device = "cpu"
        self.autocast_dtype = None

    def _embed(self, clip):
        clip = fit_clip_length(clip, self.frames_per_clip)
        track = _centroids(clip)
        return np.concatenate(
            [self.motion_weight * _motion(track), _appearance(clip)]
        ).astype(np.float32)

    def encode_clips(self, clips):
        if len(clips) == 0:
            return np.zeros((0, self.dim), dtype=np.float32)
        return l2_normalize(np.stack([self._embed(c) for c in clips])).astype(np.float32)

    @property
    def dim(self):
        return 8 + GRID * GRID

"""Pose-derived cropping (localization without a person detector).

Why. V-JEPA mean-pools every patch token, so a behaviour occupying ~2% of a
1280x720 frame is averaged against 98% unchanged room and vanishes. Measured on
real footage, hair-twirling scored BELOW background. Cropping to the subject
raises that share by roughly an order of magnitude.

It fixes a second problem at the same time. Reference clips and target footage
are rarely framed alike -- here the references are tight close-ups and the eval
video is a medium shot in a room -- and shot scale moves the embedding a long
way. Cropping BOTH sides by the same rule normalizes framing away.

No person detector is involved: the box comes from pose keypoints that are
already computed for the pose stream, so this costs nothing extra.

Two rules matter more than the box itself:

  * The identical rule must apply to reference and target. A crop applied to
    only one side is a third encoding path, and spec 14.1 names divergence
    between paths as the most common silent failure here.
  * Boxes must be temporally smooth. A box that jitters chunk to chunk injects
    apparent motion the encoder reads as signal, which is exactly the thing we
    are trying to measure.
"""

import numpy as np

# Landmarks that bound the behaviours of interest: head, shoulders, arms, hands.
# Hips and legs would drag the box down over empty torso and shrink the part
# that matters.
_HEAD = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
_ARMS = [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22]
BOX_LANDMARKS = _HEAD + _ARMS

FULL_FRAME = (0.0, 0.0, 1.0, 1.0)


def box_from_landmarks(frames, padding=0.35, min_size=0.25, aspect=1.0):
    """Bounding box over a clip's landmarks, in normalized image coords.

    frames: [T, K, 3] raw (un-normalized) landmarks, NaN where absent.
    Returns (x0, y0, x1, y1), or FULL_FRAME when no pose was found.

    The box spans the WHOLE clip rather than one frame, so a gesture that moves
    stays inside it for the clip's duration -- a per-frame box would track the
    hand and cancel the very motion being encoded.

    Landmarks are CLIPPED to the frame first. MediaPipe extrapolates occluded and
    out-of-shot joints to coordinates outside [0, 1] -- measured on real footage,
    a seated subject produced a hip landmark at y = 1.94, which inflated the
    box to 137% of the frame and silently turned every crop into a no-op.

    `aspect` (source W/H) is accepted for call compatibility and deliberately
    unused: a box that is square in NORMALIZED coordinates already has the
    source frame's pixel aspect ratio, so no correction belongs here. An earlier
    version multiplied by it and inverted the relationship.
    """
    frames = np.asarray(frames, dtype=np.float32)
    if frames.ndim != 3:
        raise ValueError("expected [T, K, 3] landmarks, got %r" % (frames.shape,))

    usable = [i for i in BOX_LANDMARKS if i < frames.shape[1]]
    points = frames[:, usable, :2].reshape(-1, 2)
    points = points[~np.isnan(points).any(axis=1)]
    if len(points) == 0:
        return FULL_FRAME
    points = np.clip(points, 0.0, 1.0)

    x0, y0 = points.min(axis=0)
    x1, y1 = points.max(axis=0)

    # Pad outward proportionally to the box, so the crop keeps context and
    # tolerates keypoints that sit slightly inside the real silhouette.
    w, h = max(x1 - x0, 1e-3), max(y1 - y0, 1e-3)
    x0, x1 = x0 - w * padding, x1 + w * padding
    y0, y1 = y0 - h * padding, y1 + h * padding

    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    w, h = x1 - x0, y1 - y0

    # Square in normalized coords == the source frame's aspect ratio, so the
    # crop is never stretched relative to an uncropped reference.
    side = max(max(w, h), min_size)
    w = h = side

    x0, x1 = cx - w / 2.0, cx + w / 2.0
    y0, y1 = cy - h / 2.0, cy + h / 2.0

    # Shift rather than clamp, so a subject near an edge keeps the full box size.
    if x0 < 0: x1, x0 = x1 - x0, 0.0
    if y0 < 0: y1, y0 = y1 - y0, 0.0
    if x1 > 1: x0, x1 = x0 - (x1 - 1.0), 1.0
    if y1 > 1: y0, y1 = y0 - (y1 - 1.0), 1.0

    return (float(max(x0, 0.0)), float(max(y0, 0.0)),
            float(min(x1, 1.0)), float(min(y1, 1.0)))


def smooth_boxes(boxes, window=5):
    """Median-filter boxes across chunks.

    Per-chunk boxes wobble with keypoint noise. That wobble is a slow zoom and
    pan applied to the encoder's input -- apparent motion with no cause in the
    scene. Median (not mean) so one bad chunk cannot drag its neighbours.
    """
    boxes = np.asarray(boxes, dtype=np.float32)
    if len(boxes) <= 2 or window <= 1:
        return boxes
    half = max(1, int(window) // 2)
    out = np.empty_like(boxes)
    for i in range(len(boxes)):
        lo, hi = max(0, i - half), min(len(boxes), i + half + 1)
        out[i] = np.median(boxes[lo:hi], axis=0)
    return out


def apply_box(frames, box):
    """Crop uint8 frames [T, H, W, 3] by a normalized box."""
    if box is None:
        return frames
    x0, y0, x1, y1 = box
    h, w = frames.shape[1:3]
    a, b = int(round(y0 * h)), int(round(y1 * h))
    c, d = int(round(x0 * w)), int(round(x1 * w))
    a, c = max(a, 0), max(c, 0)
    b, d = min(max(b, a + 8), h), min(max(d, c + 8), w)
    return frames[:, a:b, c:d]


def crop_tag(cfg):
    """Cache key fragment. Changing crop settings must invalidate features."""
    crop = cfg.get("crop", {})
    if not crop.get("enabled", False):
        return "none"
    return "pose_p%.2f_m%.2f_s%d" % (
        float(crop.get("padding", 0.35)),
        float(crop.get("min_size", 0.25)),
        int(crop.get("smooth_window", 5)),
    )


def enabled(cfg):
    return bool(cfg.get("crop", {}).get("enabled", False))

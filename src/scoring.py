"""Window pooling, similarity, and grouping (spec 7).

Windows are built by mean-pooling CACHED adjacent chunk features -- the encoder
is never re-run on overlapping spans (spec 5, pitfall 14.3).

Scores from every window scale land on one shared time grid, and that grid is
the chunk index: a window covering chunks [i, i+W) contributes its score to each
chunk it covers, and the grid takes the max. That is what makes "a run of
overlapping high-scoring windows recovers the full extent" (spec 7.3) true --
the action's chunks are covered by many windows, background chunks by few.

The hysteresis state machine in HysteresisTracker is deliberately incremental so
the offline and live paths run the SAME grouping code (spec 1, 10.2).
"""

import numpy as np

from src.encoder import l2_normalize
from src.event_log import new_event_id


# -- 7.1 window pooling ----------------------------------------------------


def pool_windows(feats, window_chunks, stride_chunks):
    """Mean-pool adjacent chunk features into windows, then re-normalize.

    Returns (windows [n_win, D], first_chunk_idx [n_win]). Re-normalizing after
    pooling is not optional -- cosine similarity against unnormalized vectors is
    meaningless (spec 14.2).
    """
    n_chunks = len(feats)
    if n_chunks == 0:
        return np.zeros((0, feats.shape[1]), np.float32), np.zeros(0, np.int64)

    window_chunks = max(1, min(int(window_chunks), n_chunks))
    stride_chunks = max(1, int(stride_chunks))

    starts = np.arange(0, n_chunks - window_chunks + 1, stride_chunks, dtype=np.int64)
    if len(starts) == 0:
        starts = np.zeros(1, dtype=np.int64)

    # Cumulative sums give every window's mean in one pass.
    cumulative = np.concatenate([np.zeros((1, feats.shape[1]), np.float32), np.cumsum(feats, axis=0)])
    pooled = (cumulative[starts + window_chunks] - cumulative[starts]) / float(window_chunks)
    return l2_normalize(pooled), starts


# -- 7.2 similarity --------------------------------------------------------


def similarity_to_class(windows, class_vectors, topk=3):
    """Mean of the top-k cosine similarities against a class's variants.

    More robust than mean-of-all (dragged down by off-distribution
    augmentations) and than max (noisy) -- spec 7.2.
    """
    if len(windows) == 0:
        return np.zeros(0, np.float32)
    sims = windows @ class_vectors.T                      # [n_win, n_variants]
    k = int(min(max(1, topk), sims.shape[1]))
    # argpartition puts the k largest in the tail; we only need their mean.
    top = np.partition(sims, -k, axis=1)[:, -k:]
    return top.mean(axis=1).astype(np.float32)


def score_grid(feats, bank, cfg, window_lengths=None, topk=None):
    """Per-chunk, per-class score after max-pooling over window scales.

    Returns [n_class, n_chunks] aligned to the class order of sorted(bank).
    """
    class_names = sorted(bank.keys())
    n_chunks = len(feats)
    grid = np.full((len(class_names), n_chunks), -np.inf, dtype=np.float32)
    if n_chunks == 0:
        return class_names, grid

    lengths = window_lengths if window_lengths is not None else cfg.window_lengths_chunks()
    topk = cfg["topk_prototypes"] if topk is None else topk
    stride = cfg.stride_chunks

    for window_chunks in lengths:
        windows, starts = pool_windows(feats, window_chunks, stride)
        span = max(1, min(int(window_chunks), n_chunks))
        for ci, name in enumerate(class_names):
            scores = similarity_to_class(windows, bank[name], topk)
            for score, start in zip(scores, starts):
                stop = min(start + span, n_chunks)
                np.maximum(grid[ci, start:stop], score, out=grid[ci, start:stop])

    # Chunks no window reached (possible only when n_chunks < window) score 0.
    grid[np.isneginf(grid)] = 0.0
    return class_names, grid


def aggregate_per_chunk(series, cfg, window_lengths=None):
    """Apply the embedding path's multi-scale windowing to a per-chunk series.

    The pose stream produces one score per chunk directly, with no pooling step.
    Running it through the same window/stride/max-assign structure keeps the two
    streams on the same temporal footing -- otherwise pose would react a chunk
    earlier than V-JEPA and fusion would smear every boundary.
    """
    series = np.asarray(series, dtype=np.float32)
    n_chunks = len(series)
    if n_chunks == 0:
        return series

    lengths = window_lengths if window_lengths is not None else cfg.window_lengths_chunks()
    stride = cfg.stride_chunks
    out = np.full(n_chunks, -np.inf, dtype=np.float32)

    cumulative = np.concatenate([[0.0], np.cumsum(series.astype(np.float64))])
    for window_chunks in lengths:
        span = max(1, min(int(window_chunks), n_chunks))
        starts = np.arange(0, n_chunks - span + 1, stride, dtype=np.int64)
        if len(starts) == 0:
            starts = np.zeros(1, dtype=np.int64)
        means = (cumulative[starts + span] - cumulative[starts]) / float(span)
        for mean, start in zip(means, starts):
            stop = min(start + span, n_chunks)
            np.maximum(out[start:stop], np.float32(mean), out=out[start:stop])

    out[np.isneginf(out)] = 0.0
    return out


def stream_weights(cfg, class_name, available):
    """Per-class mixing weights over the available streams, normalized to 1.

    Config may give explicit `weights: {vjepa: .., pose: .., hands: ..}`. When it
    does not, `alpha_vjepa` is honoured as the two-way split it always meant,
    and any third stream shares the non-appearance half.
    """
    class_cfg = cfg.class_cfg(class_name)
    explicit = class_cfg.get("weights")
    if explicit:
        weights = {k: float(explicit.get(k, 0.0)) for k in available}
    else:
        alpha = float(class_cfg.get("alpha_vjepa", 1.0))
        alpha = min(max(alpha, 0.0), 1.0)
        others = [k for k in available if k != "vjepa"]
        weights = {"vjepa": alpha} if "vjepa" in available else {}
        for k in others:
            weights[k] = (1.0 - alpha) / len(others) if others else 0.0

    total = sum(weights.values())
    if total <= 0:
        return {k: 1.0 / len(available) for k in available}
    return {k: v / total for k, v in weights.items()}


def combined_grid(cfg, bank, feats_bundle, pose_bundle=None, pose_templates=None,
                  hand_bundle=None, hand_templates=None):
    """The score grid every caller should use: appearance, optionally fused
    with pose and hand streams.

    Returns (class_names, fused_grid, parts) where parts holds the un-fused
    grids so diagnostics can report each stream separately.
    """
    class_names, vjepa = score_grid(feats_bundle["feats"], bank, cfg)
    n = vjepa.shape[1]
    parts = {"vjepa": vjepa}

    def add(name, bundle, templates, module):
        nonlocal n
        if bundle is None or not templates:
            return
        temperature = float(cfg.get(name, {}).get("temperature", 0.35))
        raw = module.score_chunks(bundle["sequences"], templates, class_names, temperature)
        n = min(n, raw.shape[1])
        parts[name] = raw

    if pose_bundle is not None and pose_templates:
        from src import pose as pose_module

        add("pose", pose_bundle, pose_templates, pose_module)
    if hand_bundle is not None and hand_templates:
        from src import hands as hands_module

        add("hands", hand_bundle, hand_templates, hands_module)

    # Every stream must describe the same chunks before they can be mixed.
    for key in list(parts):
        parts[key] = parts[key][:, :n]

    # Keypoint streams score per chunk with no pooling of their own; run them
    # through the embedding path's windowing so all streams sit on the same
    # temporal footing.
    for key in ("pose", "hands"):
        if key in parts:
            parts[key] = np.stack(
                [aggregate_per_chunk(parts[key][i], cfg) for i in range(len(class_names))]
            )

    fused = fuse_scores(class_names, parts, cfg)
    parts.setdefault("pose", None)
    parts.setdefault("hands", None)
    return class_names, fused, parts


def fuse_scores(class_names, streams, cfg):
    """Late score-level fusion, per class (spec 11.3).

        score_c = sum_s w_c,s * z(sim_s)

    Every stream is standardized against its own background BEFORE mixing.
    Without that they have incomparable scales -- V-JEPA cosines cluster near
    0.9, keypoint similarities span most of (0, 1] -- and whichever has the
    larger spread silently decides every detection.

    Accepts a bare grid for the appearance-only case.
    """
    from src.normalize import zscore_grid

    if not isinstance(streams, dict):
        streams = {"vjepa": streams}
    streams = {k: v for k, v in streams.items() if v is not None}

    normalize = bool(cfg.get("normalize_scores", True))
    if len(streams) == 1 and "vjepa" in streams:
        return zscore_grid(streams["vjepa"]) if normalize else streams["vjepa"]

    z = {k: zscore_grid(v) for k, v in streams.items()}
    available = sorted(z)
    fused = np.zeros_like(z[available[0]])
    for ci, name in enumerate(class_names):
        weights = stream_weights(cfg, name, available)
        for key in available:
            fused[ci] += weights[key] * z[key][ci]
    return fused


def smooth(series, width):
    """Centered moving average, edges replicated (spec 7.3.1)."""
    width = int(width)
    if width <= 1 or len(series) == 0:
        return np.asarray(series, dtype=np.float32)
    pad = width // 2
    padded = np.pad(np.asarray(series, dtype=np.float32), (pad, pad), mode="edge")
    kernel = np.ones(width, dtype=np.float32) / width
    return np.convolve(padded, kernel, mode="valid")[: len(series)].astype(np.float32)


# -- 7.3 grouping ----------------------------------------------------------


class HysteresisTracker:
    """Open above tau_high, stay open above tau_low, close below (spec 7.3.2).

    Incremental by construction: offline drives it over a whole score series,
    live drives it one chunk at a time as features arrive. Neither can see the
    future, so both produce the same events for the same input.
    """

    def __init__(self, class_name, tau_high, tau_low, max_open_sec=None):
        self.class_name = class_name
        self.tau_high = float(tau_high)
        self.tau_low = float(tau_low)
        self.max_open_sec = max_open_sec

        self.event_id = None
        self.start_sec = None
        self.peak_score = None
        self.last_score = 0.0

    @property
    def is_open(self):
        return self.event_id is not None

    def _event(self, end_sec, score):
        return {
            "event_id": self.event_id,
            "class": self.class_name,
            "start": float(self.start_sec),
            "end": float(end_sec),
            "score": float(score),
        }

    def _reset(self):
        self.event_id = None
        self.start_sec = None
        self.peak_score = None

    def step(self, start_sec, end_sec, score, allow_open=True):
        """Advance one time position.

        Returns (opened, closed): `opened` is a provisional event dict on the
        step that opened a detection (live mode writes its open row from this),
        `closed` is the finished event on the step that closed one.

        allow_open=False suppresses opening while still tracking the score --
        the live path uses it for cross-class resolution, where a lower-scoring
        class must not open alongside a higher-scoring one (spec 7.3.5).
        """
        score = float(score)
        self.last_score = score
        opened = closed = None

        if not self.is_open:
            if score > self.tau_high and allow_open:
                self.event_id = new_event_id()
                self.start_sec = float(start_sec)
                self.peak_score = score
                opened = {
                    "event_id": self.event_id,
                    "class": self.class_name,
                    "start": self.start_sec,
                    "end": None,
                    "score": score,
                }
            return opened, closed

        self.peak_score = max(self.peak_score, score)

        # Force-close a detection that has run past the limit (spec 9.4). If
        # this fires often the thresholds are wrong; raising the limit hides it.
        if self.max_open_sec is not None and (end_sec - self.start_sec) >= self.max_open_sec:
            closed = self._event(self.start_sec + self.max_open_sec, score)
            closed["forced"] = True
            self._reset()
            return opened, closed

        if score <= self.tau_low:
            closed = self._event(end_sec, self.peak_score)
            self._reset()

        return opened, closed

    def flush(self, end_sec):
        """Close whatever is still open at the end of a stream."""
        if not self.is_open:
            return None
        closed = self._event(end_sec, self.peak_score)
        self._reset()
        return closed


def tiou(a, b):
    """Temporal IoU of two (start, end) spans."""
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def temporal_nms(detections, threshold):
    """Merge same-class detections overlapping by tIoU > threshold (spec 7.3.4)."""
    kept = []
    for det in sorted(detections, key=lambda d: d["score"], reverse=True):
        span = (det["start"], det["end"])
        if any(
            k["class"] == det["class"] and tiou(span, (k["start"], k["end"])) > threshold
            for k in kept
        ):
            continue
        kept.append(det)
    return kept


def resolve_cross_class(detections):
    """Where classes overlap in time, keep the highest-scoring class (spec 7.3.5)."""
    kept = []
    for det in sorted(detections, key=lambda d: d["score"], reverse=True):
        span = (det["start"], det["end"])
        overlaps_other_class = any(
            k["class"] != det["class"]
            and min(span[1], k["end"]) - max(span[0], k["start"]) > 0
            for k in kept
        )
        if not overlaps_other_class:
            kept.append(det)
    return kept


def class_tau(cfg, class_name, tau_high):
    """Per-class open/close thresholds.

    `tau_scale` under classes.<name> multiplies the global tau_high so a noisy
    class (e.g. hair_twirling) can sit higher without raising the bar for
    classes that already work (head_nodding). tau_low stays the same ratio of
    that class's effective tau_high.
    """
    scale = float(cfg.class_cfg(class_name).get("tau_scale", 1.0))
    th = float(tau_high) * max(scale, 0.0)
    tl = th * float(cfg["tau_low_ratio"])
    return th, tl


def group_detections(class_names, grid, starts, cfg, tau_high, chunk_sec=None):
    """Score grid -> detections. The offline path (spec 7.3, all six steps).

    Live mode runs steps 1-3 only; see live.py and spec 10.2 for why NMS and
    cross-class resolution cannot run online.
    """
    chunk_sec = float(chunk_sec if chunk_sec is not None else cfg["chunk_sec"])
    min_duration = float(cfg["min_duration_ratio"]) * float(cfg["w_base_sec"])

    detections = []
    for ci, name in enumerate(class_names):
        series = smooth(grid[ci], cfg["smoothing_windows"])
        th, tl = class_tau(cfg, name, tau_high)
        tracker = HysteresisTracker(name, th, tl)
        for t, score in enumerate(series):
            start_sec = float(starts[t])
            _, closed = tracker.step(start_sec, start_sec + chunk_sec, score)
            if closed:
                detections.append(closed)
        final = tracker.flush(float(starts[-1]) + chunk_sec)
        if final:
            detections.append(final)

    # 3. minimum duration
    detections = [d for d in detections if (d["end"] - d["start"]) >= min_duration]
    # 4. temporal NMS, then 5. cross-class resolution
    detections = temporal_nms(detections, float(cfg["nms_tiou"]))
    detections = resolve_cross_class(detections)
    return sorted(detections, key=lambda d: d["start"])


def background_score_stats(grid):
    """Score distribution summary, for pitfall 14.8.

    If background scores sit close to action scores no threshold will help, and
    that is the signal to move to Phase 5 -- so detect.py always prints this.
    """
    flat = np.asarray(grid, dtype=np.float32).ravel()
    if flat.size == 0:
        return {}
    return {
        "min": float(flat.min()),
        "p50": float(np.percentile(flat, 50)),
        "p90": float(np.percentile(flat, 90)),
        "p99": float(np.percentile(flat, 99)),
        "max": float(flat.max()),
    }

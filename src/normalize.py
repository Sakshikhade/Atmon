"""Background-relative score normalization.

Raw cosine similarities from a single scene cluster near 1.0 -- measured on real
footage, background sat at 0.89 and action at 0.92. Two consequences:

  * an absolute threshold is meaningless across scenes, because the whole band
    shifts when the room, lighting or distance changes;
  * `tau_low = tau_high x 0.85` lands far below background, so a detection opens
    and never closes (spec 7.3.2 assumes scores span a wide range from zero).

Standardizing against the background distribution fixes both. Scores become
"sigma above background", which is comparable across scenes AND across classes,
and a tau_low ratio then operates on a scale where zero means "typical
background" rather than "no similarity at all".

Center and spread are estimated ROBUSTLY (median and MAD) because the series
being standardized contains the actions too. That works while background is the
majority of the footage -- which the eval set is required to be anyway (spec
8.1, "plenty of background"). If actions dominate, the center drifts up and
detections are suppressed; `check_separation.py` reports the action fraction so
this is visible rather than silent.
"""

import collections

import numpy as np

MAD_TO_SIGMA = 1.4826  # makes MAD a consistent estimator of sigma for normal data

# Floor on the estimated spread. Every score standardized here is a similarity
# in roughly [0, 1] -- cosine against prototypes, or exp(-DTW) -- whose genuine
# background spread runs about 0.01-0.05. A floor far below that lets a
# near-degenerate estimate through: measured live, a subject sitting still
# through warmup produced a MAD near zero and scores of 11 and 16 sigma where
# offline peaks were 2.9.
#
# CONSTANT, deliberately. A floor proportional to the magnitude of the data
# would make the same pattern standardize differently depending on which band it
# sits in, destroying the scene invariance that is the whole reason for
# standardizing. A constant is shift-invariant, so it is safe here.
MIN_SCALE = 0.005


def robust_center_scale(values, min_scale=MIN_SCALE):
    """(center, scale) from median and MAD. Resistant to the action minority.

    The floor is ABSOLUTE, deliberately. A floor proportional to the magnitude
    of the data would make the same pattern standardize differently depending on
    which band it sits in -- destroying the scene invariance that is the whole
    reason for standardizing. Degenerate estimates are handled by refusing to
    trust them (see RunningBackground.ready), not by inflating the scale.
    """
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return 0.0, 1.0
    center = float(np.median(values))
    mad = float(np.median(np.abs(values - center)))
    scale = max(mad * MAD_TO_SIGMA, min_scale)
    return center, scale


def robust_spread(values):
    """MAD-derived spread with NO floor -- for judging whether an estimate is
    usable at all, as distinct from the floored scale used to divide by."""
    values = np.asarray(values, dtype=np.float64)
    if values.size == 0:
        return 0.0
    center = float(np.median(values))
    return float(np.median(np.abs(values - center)) * MAD_TO_SIGMA)


def zscore_series(series, center=None, scale=None):
    """Standardize one class's score series into sigma-above-background."""
    series = np.asarray(series, dtype=np.float32)
    if series.size == 0:
        return series
    if center is None or scale is None:
        center, scale = robust_center_scale(series)
    return ((series - center) / scale).astype(np.float32)


def zscore_grid(grid):
    """Standardize each class row independently.

    Per class, not globally: a class whose prototype happens to sit closer to
    this scene would otherwise dominate every comparison for a reason that has
    nothing to do with the behaviour being present.
    """
    grid = np.asarray(grid, dtype=np.float32)
    out = np.empty_like(grid)
    for i in range(grid.shape[0]):
        out[i] = zscore_series(grid[i])
    return out


class RunningBackground:
    """Streaming version for the live path.

    Offline can standardize against the whole video; live cannot see the future,
    so it keeps a bounded history of recent raw scores and re-estimates from
    that. Until `warmup` samples have arrived the estimate is not trustworthy
    and `ready` stays False -- callers should not open detections before then,
    or the first minute of every session produces noise.
    """

    def __init__(self, window=300, warmup=30, min_scale=MIN_SCALE, fixed_scale=None):
        self.history = collections.deque(maxlen=int(window))
        self.warmup = int(warmup)
        self.min_scale = float(min_scale)
        # A calibrated scale, when available, replaces the running estimate of
        # spread. Measured on real footage, background similarity varies over
        # MINUTES: a 30s window sees about a sixth of the true spread (0.019 vs
        # 0.123), so live divided by a number ~6x too small and every score
        # inflated by the same factor -- detections opened trivially and could
        # not fall back below tau_low.
        #
        # Spread is a property of the behaviour and the backbone; the CENTRE is
        # what moves when the room or lighting changes. So the scale is taken
        # from calibration and only the centre is tracked, which needs a handful
        # of chunks rather than four minutes.
        self.fixed_scale = None if fixed_scale is None else float(fixed_scale)
        self._center = 0.0
        self._scale = 1.0
        self._dirty = True

    def update(self, raw_score):
        self.history.append(float(raw_score))
        self._dirty = True

    @property
    def ready(self):
        """Enough samples AND actual spread among them.

        Sample count alone is not enough. A handful of near-identical scores --
        the opening chunks of a live session, before anything has varied --
        gives a MAD of zero, the scale falls to its floor, and the next ordinary
        fluctuation becomes hundreds of sigma. Measured live, that fired a
        detection the instant warmup ended. An estimate with no spread is not a
        usable estimate, however many samples produced it.
        """
        if len(self.history) < self.warmup:
            return False
        if self.fixed_scale is not None:
            # Only the centre is being estimated, and a median converges fast.
            return True
        # Spread BEFORE flooring: with a floor applied, a wholly flat estimate
        # would sit exactly at the floor and could never be distinguished from a
        # narrow but real one.
        return robust_spread(list(self.history)) > 0.0

    def _refresh(self):
        if self._dirty and self.history:
            center, scale = robust_center_scale(list(self.history))
            self._center = center
            self._scale = self.fixed_scale if self.fixed_scale is not None else scale
            self._dirty = False

    def normalize(self, raw_score):
        self._refresh()
        return float((raw_score - self._center) / self._scale)

    @property
    def stats(self):
        self._refresh()
        return self._center, self._scale


def describe(grid, class_names):
    """One line per class, for logs."""
    lines = []
    for i, name in enumerate(class_names):
        row = np.asarray(grid[i])
        lines.append(
            "  %-16s median %+.2f  p90 %+.2f  max %+.2f (sigma)"
            % (name, float(np.median(row)), float(np.percentile(row, 90)), float(row.max()))
        )
    return "\n".join(lines)

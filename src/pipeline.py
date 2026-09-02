"""Shared detection/event-transition core.

Extracted from the live monitor loop so it can be reused by any consumer that
runs the detection pipeline — the live app (`main.py`) and the recorded-video
ingest CLI (Epic 3). Keeping this logic in one place means both paths detect and
record events identically.

Two pieces:
- :func:`compute_normalized_distance` — the scale-invariant wrist-to-shoulder
  distance used as the classifier's primary signal.
- :class:`EventTracker` — owns the per-behavior start/resolved state machine and
  emits discrete transition events (with start/end/duration + type/subtype/action).

This module is pure (no cv2/HUD/network) and fully unit-tested.
"""

import time
from dataclasses import dataclass, field

# Pose landmark indices (MediaPipe).
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_WRIST, RIGHT_WRIST = 15, 16


def build_detectors(
    mode: str = "heuristic", model_path: str = "models/stimming_classifier.pkl", fps: float = 30.0
):
    """Construct the shared detection components used by both the live app and
    the ingest CLI, so their configuration cannot drift.

    Returns ``(extractor, classifier, ears_closed_detector)``.

    Imports are done lazily inside the function so this module stays import-light
    (the pure distance/EventTracker logic is unit-tested without the CV stack).
    """
    # Local imports: these pull in cv2/mediapipe/sklearn — only needed at runtime.
    from src.classifier import BehaviorClassifier
    from src.ears_closed_stimming import EarsClosedStimmingDetector
    from src.feature_extractor import SkeletalFeatureExtractor
    from src.ml_classifier import MLBehaviorClassifier

    extractor = SkeletalFeatureExtractor(
        min_detection_confidence=0.5, min_presence_confidence=0.5, min_tracking_confidence=0.5
    )

    if mode == "ml":
        classifier = MLBehaviorClassifier(window_size=60, fps=fps, model_path=model_path)
    else:
        classifier = BehaviorClassifier(
            window_size=60,
            fps=fps,
            freq_min=3.0,
            freq_max=6.5,
            velocity_threshold=0.04,
            sustained_duration=1.5,
        )

    ears_closed_detector = EarsClosedStimmingDetector(
        fps=fps, hand_to_ear_threshold=0.18, required_duration=2.0, eye_closed_threshold=0.20
    )
    return extractor, classifier, ears_closed_detector


def _dist3(a, b) -> float:
    return ((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2) ** 0.5


def compute_normalized_distance(pose_lm) -> float:
    """Max wrist-to-shoulder distance, normalized by shoulder width.

    Mirrors the live loop: uses the larger of the two arms' normalized distances
    so movement from either hand is captured. Returns 0.0 if landmarks are
    missing or the subject's shoulders are coincident (degenerate scale).
    """
    if not pose_lm or len(pose_lm) <= RIGHT_WRIST:
        return 0.0
    l_shoulder = pose_lm[LEFT_SHOULDER]
    r_shoulder = pose_lm[RIGHT_SHOULDER]
    l_wrist = pose_lm[LEFT_WRIST]
    r_wrist = pose_lm[RIGHT_WRIST]

    shoulder_width = _dist3(l_shoulder, r_shoulder)
    if shoulder_width <= 0:
        return 0.0

    norm_l = _dist3(l_wrist, l_shoulder) / shoulder_width
    norm_r = _dist3(r_wrist, r_shoulder) / shoulder_width
    return max(norm_l, norm_r)


@dataclass
class TransitionEvent:
    """A discrete behavior transition emitted by :class:`EventTracker`."""

    behavior: str  # "stimming" | "ears_covered" | "eyes_closed"
    phase: str  # "start" | "resolved"
    event_type: str  # domain type, e.g. "stimming" | "avoidance"
    subtype: str
    action: str
    started_at: float | None  # epoch seconds (None for a start phase's own event)
    ended_at: float | None
    duration_s: float


# Static per-behavior labels (type, subtype, action) used on resolved events.
_BEHAVIOR_LABELS = {
    "stimming": ("stimming", "rhythmic_stimming", "repetitive_wrist_motion"),
    "ears_covered": ("avoidance", "ears_covered_avoidance", "hand_to_ear"),
    "eyes_closed": ("avoidance", "eyes_closed_avoidance", "eyes_closed"),
}


@dataclass
class EventTracker:
    """Edge-triggered state machine for the three tracked behaviors.

    Feed :meth:`update` the current per-frame boolean states; it returns a list of
    :class:`TransitionEvent` for any rising (``start``) or falling (``resolved``)
    edges since the last call. Start times are recorded so resolved events carry a
    duration — matching the live loop's behavior exactly.

    ``ears_covered`` / ``eyes_closed`` are suppressed while ``ears_closed_stimming``
    is active (the caller passes the already-resolved booleans, mirroring main.py).
    """

    _active: dict = field(
        default_factory=lambda: {
            "stimming": False,
            "ears_covered": False,
            "eyes_closed": False,
        }
    )
    _start_times: dict = field(default_factory=dict)

    def update(
        self,
        *,
        stimming: bool,
        ears_covered: bool,
        eyes_closed: bool,
        now: float | None = None,
    ) -> list[TransitionEvent]:
        now = time.time() if now is None else now
        current = {
            "stimming": bool(stimming),
            "ears_covered": bool(ears_covered),
            "eyes_closed": bool(eyes_closed),
        }
        events: list[TransitionEvent] = []

        for behavior, is_on in current.items():
            was_on = self._active[behavior]
            event_type, subtype, action = _BEHAVIOR_LABELS[behavior]

            if is_on and not was_on:
                # Rising edge — behavior started.
                self._start_times[behavior] = now
                self._active[behavior] = True
                events.append(
                    TransitionEvent(
                        behavior=behavior,
                        phase="start",
                        event_type=event_type,
                        subtype=subtype,
                        action=action,
                        started_at=now,
                        ended_at=None,
                        duration_s=0.0,
                    )
                )
            elif not is_on and was_on:
                # Falling edge — behavior resolved.
                start = self._start_times.get(behavior)
                duration = (now - start) if start is not None else 0.0
                self._active[behavior] = False
                self._start_times.pop(behavior, None)
                events.append(
                    TransitionEvent(
                        behavior=behavior,
                        phase="resolved",
                        event_type=event_type,
                        subtype=subtype,
                        action=action,
                        started_at=start,
                        ended_at=now,
                        duration_s=duration,
                    )
                )

        return events

    def active(self, behavior: str) -> bool:
        """Whether the given behavior is currently in the 'on' state."""
        return self._active.get(behavior, False)

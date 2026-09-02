import os
import pickle
import time
from collections import deque

import numpy as np

from src.classifier import BehaviorClassifier
from src.logging_config import get_logger

logger = get_logger(__name__)


class MLBehaviorClassifier:
    """
    MLBehaviorClassifier wraps a scikit-learn trained model (Random Forest)
    to perform real-time skeletal motion classification. It extracts identical statistical
    features over a 60-frame rolling window and formats the inference output
    to match the rule-based BehaviorClassifier contract.
    """

    def __init__(self, window_size=60, fps=30.0, model_path="models/stimming_classifier.pkl"):
        self.window_size = window_size
        self._fps = fps
        self.model_path = model_path
        self.model = None
        self.fallback_classifier = BehaviorClassifier(
            window_size=window_size,
            fps=fps,
            freq_min=3.0,
            freq_max=6.5,
            velocity_threshold=0.04,
            sustained_duration=1.5,
        )

        # Initialize deques for normalized distances, velocities, ear coverage, and eye openness
        self.normalized_dists = deque(maxlen=window_size)
        self.velocities = deque(maxlen=window_size)
        self.ear_coverage_dists = deque(maxlen=window_size)
        self.eye_openness_ratios = deque(maxlen=window_size)

        # Timing and state variables
        self.consecutive_frames_met = 0
        self.stimming_event_start_time = None
        self.is_stimming = False
        self.is_avoidance = False
        self.ears_covered = False
        self.eyes_closed = False

        # Sliding window for smoothing predictions (like heuristic) over a shorter responsive interval
        self.activation_window_duration = 3.0
        self.activation_window = deque(maxlen=int(self.activation_window_duration * fps))

        # Load pre-trained Random Forest model
        self.load_model()

    @property
    def fps(self):
        return self._fps

    @fps.setter
    def fps(self, value):
        if value <= 0:
            return
        self._fps = value
        if self.fallback_classifier:
            self.fallback_classifier.fps = value

        # Update activation window maxlen while preserving history
        new_maxlen = int(self.activation_window_duration * value)
        if self.activation_window.maxlen != new_maxlen:
            new_window = deque(self.activation_window, maxlen=new_maxlen)
            self.activation_window = new_window

    def load_model(self):
        """Loads the serialized Random Forest model from disk."""
        if os.path.exists(self.model_path):
            try:
                with open(self.model_path, "rb") as f:
                    self.model = pickle.load(f)
                logger.info("Loaded Random Forest model successfully from %s", self.model_path)
            except Exception as e:
                logger.warning(
                    "Failed to load model from %s: %s. Falling back to rule-based Heuristics.",
                    self.model_path,
                    e,
                )
        else:
            logger.info(
                "Model %s not found. Running in rule-based Heuristic fallback mode.",
                self.model_path,
            )

    def reset_tracking(self):
        """Resets the rolling deques and prediction trackers."""
        self.normalized_dists.clear()
        self.velocities.clear()
        self.ear_coverage_dists.clear()
        self.eye_openness_ratios.clear()
        self.activation_window.clear()
        self.consecutive_frames_met = 0
        self.stimming_event_start_time = None
        self.is_stimming = False
        self.is_avoidance = False
        self.ears_covered = False
        self.eyes_closed = False
        self.fallback_classifier.reset_tracking()

    def update(self, normalized_dist, ear_coverage_dist=999.0, eye_openness_ratio=1.0):
        """
        Extracts temporal statistics, performs ML classification,
        and returns detection metrics. Falls back to Heuristics if no model is loaded.
        """
        if self.model is None:
            # Fall back to heuristic rule-based engine
            return self.fallback_classifier.update(
                normalized_dist, ear_coverage_dist, eye_openness_ratio
            )

        # Compute velocity relative to previous distance
        if len(self.normalized_dists) > 0:
            prev_dist = self.normalized_dists[-1]
            velocity = abs(normalized_dist - prev_dist)
        else:
            velocity = 0.0

        # Append to sliding windows
        self.normalized_dists.append(normalized_dist)
        self.velocities.append(velocity)
        self.ear_coverage_dists.append(ear_coverage_dist)
        self.eye_openness_ratios.append(eye_openness_ratio)

        q_len = len(self.normalized_dists)

        # Feature extraction
        mean_dist = float(np.mean(self.normalized_dists)) if q_len > 0 else 0.0
        std_dist = float(np.std(self.normalized_dists)) if q_len > 1 else 0.0
        mean_velocity = float(np.mean(self.velocities)) if len(self.velocities) > 0 else 0.0
        mean_ear_coverage = (
            float(np.mean(self.ear_coverage_dists)) if len(self.ear_coverage_dists) > 0 else 999.0
        )
        mean_eye_openness = (
            float(np.mean(self.eye_openness_ratios)) if len(self.eye_openness_ratios) > 0 else 1.0
        )

        # Rhythm checker / peak frequency
        peaks = 0
        if q_len >= 3:
            window_velocities = []
            for i in range(1, q_len):
                window_velocities.append(
                    abs(self.normalized_dists[i] - self.normalized_dists[i - 1])
                )

            for i in range(1, len(window_velocities) - 1):
                if (
                    window_velocities[i] > window_velocities[i - 1]
                    and window_velocities[i] > window_velocities[i + 1]
                    and window_velocities[i] > 0.005
                ):
                    peaks += 1

        if q_len > 10:
            duration_seconds = q_len / self.fps
            frequency = (peaks / 2.0) / duration_seconds
        else:
            frequency = 0.0

        # Formulate feature vector: mean_distance, std_distance, mean_velocity, peak_frequency, mean_ear_coverage, mean_eye_openness
        features = np.array(
            [[mean_dist, std_dist, mean_velocity, frequency, mean_ear_coverage, mean_eye_openness]]
        )

        # Execute Random Forest inference
        try:
            pred = int(self.model.predict(features)[0])
        except Exception:
            # Fall back to 0 on prediction failure
            pred = 0

        # Apply noise-filtering smoothing on predictions
        self.activation_window.append(pred)

        self.is_stimming = False
        self.is_avoidance = False
        self.ears_covered = False
        self.eyes_closed = False

        # Stimming/Avoidance criteria: smoothed prediction ratio >= 60% after 1.5 seconds of data
        min_frames = int(1.5 * self.fps)
        if len(self.activation_window) >= min_frames:
            # Count occurrences of each prediction type in the window
            counts = {0: 0, 1: 0, 2: 0}
            for p in self.activation_window:
                counts[p] = counts.get(p, 0) + 1

            total_window = len(self.activation_window)
            if counts[1] / total_window >= 0.60:
                self.is_stimming = True
            elif counts[2] / total_window >= 0.60:
                self.is_avoidance = True
                # Determine detail based on metrics
                if mean_ear_coverage < 0.15:
                    self.ears_covered = True
                if mean_eye_openness < 0.20:
                    self.eyes_closed = True

        # Calculate continuous duration
        elapsed_time = 0.0
        if self.is_stimming:
            current_time = time.time()
            if self.stimming_event_start_time is None:
                self.stimming_event_start_time = current_time
            self.consecutive_frames_met += 1
            elapsed_time = current_time - self.stimming_event_start_time
        else:
            self.stimming_event_start_time = None
            self.consecutive_frames_met = 0

        return {
            "normalized_dist": normalized_dist,
            "velocity": velocity,
            "mean_velocity": mean_velocity,
            "std_dist": std_dist,
            "frequency": frequency,
            "is_stimming": self.is_stimming,
            "is_avoidance": self.is_avoidance,
            "ears_covered": self.ears_covered,
            "eyes_closed": self.eyes_closed,
            "ear_coverage_dist": ear_coverage_dist,
            "eye_openness_ratio": eye_openness_ratio,
            "elapsed_sustained": elapsed_time,
            "consecutive_frames": self.consecutive_frames_met,
            "activation_ratio": sum(1 if p == 1 else 0 for p in self.activation_window)
            / len(self.activation_window)
            if len(self.activation_window) > 0
            else 0.0,
        }

import time
from collections import deque

import numpy as np


class BehaviorClassifier:
    """
    BehaviorClassifier implements a heuristic rules engine to analyze skeletal feature time-series.
    It tracks a sliding window of normalized distances and velocities, and calculates key statistics:
    - Mean velocity over the window
    - Standard deviation of the normalized distance over the window
    - Peak frequency (using peak-counting rhythm checker)

    It triggers a stimming event alert if:
    - Frequency sits between 3.0 Hz and 6.5 Hz
    - Average velocity remains above 0.04
    - Both conditions are sustained continuously for at least 1.5 seconds.

    Privacy-by-Design:
    - Operates entirely on abstract numeric coordinate/distance vectors.
    - Zero image data or personal identifiers are stored or analyzed.
    """

    def __init__(
        self,
        window_size=60,
        fps=30.0,
        freq_min=3.0,
        freq_max=6.5,
        velocity_threshold=0.04,
        sustained_duration=1.5,
    ):
        self.window_size = window_size
        self._fps = fps
        self.freq_min = freq_min
        self.freq_max = freq_max
        self.velocity_threshold = velocity_threshold
        self.sustained_duration = sustained_duration

        # Sliding windows for normalized distances and velocities
        self.normalized_dists = deque(maxlen=window_size)
        self.velocities = deque(maxlen=window_size)

        # Tracking variables for sustained heuristic trigger
        self.condition_met_start_time = None
        self.consecutive_frames_met = 0
        self.is_stimming = False

        # Sliding activation window for robust noise-tolerant triggers (3 seconds at 30 FPS = 90 frames)
        self.activation_window_duration = 3.0
        self.activation_ratio_threshold = 0.50
        self.activation_window = deque(maxlen=int(self.activation_window_duration * fps))

        # Physical Avoidance heuristic variables
        self.ear_coverage_threshold = 0.15
        self.eye_closed_threshold = 0.20
        self.ear_coverage_frames = 0
        self.eye_closed_frames = 0
        self.ears_covered = False
        self.eyes_closed = False

    @property
    def fps(self):
        return self._fps

    @fps.setter
    def fps(self, value):
        if value <= 0:
            return
        self._fps = value
        new_maxlen = int(self.activation_window_duration * value)
        if self.activation_window.maxlen != new_maxlen:
            self.activation_window = deque(self.activation_window, maxlen=new_maxlen)

    def reset_tracking(self):
        """
        Resets the sliding windows and sustained heuristic trackers.
        Called when tracking is lost to prevent stale history from causing false positives.
        """
        self.normalized_dists.clear()
        self.velocities.clear()
        self.activation_window.clear()
        self.condition_met_start_time = None
        self.consecutive_frames_met = 0
        self.is_stimming = False
        self.ear_coverage_frames = 0
        self.eye_closed_frames = 0
        self.ears_covered = False
        self.eyes_closed = False

    def update(self, normalized_dist, ear_coverage_dist=999.0, eye_openness_ratio=1.0):
        """
        Updates the sliding window with a new normalized distance, computes temporal statistics,
        and evaluates heuristic detection thresholds.

        Parameters:
            normalized_dist (float): The current frame's wrist-to-shoulder normalized distance.
            ear_coverage_dist (float): Left or Right wrist to Ear normalized distance.
            eye_openness_ratio (float): Ratio of eye openness (1.0 is open, < 0.2 is closed).

        Returns:
            dict: Classification metrics and detection status.
        """
        # Calculate velocity relative to previous normalized distance
        if len(self.normalized_dists) > 0:
            prev_dist = self.normalized_dists[-1]
            velocity = abs(normalized_dist - prev_dist)
        else:
            velocity = 0.0

        # Append to sliding windows
        self.normalized_dists.append(normalized_dist)
        self.velocities.append(velocity)

        # Compute temporal statistics
        q_len = len(self.normalized_dists)

        # 1. Mean velocity over the sliding window
        if len(self.velocities) > 0:
            mean_velocity = float(np.mean(self.velocities))
        else:
            mean_velocity = 0.0

        # 2. Standard deviation of normalized distance over the sliding window
        if q_len > 1:
            std_dist = float(np.std(self.normalized_dists))
        else:
            std_dist = 0.0

        # 3. Peak frequency (using rhythm checker loop over window)
        peaks = 0
        if q_len >= 3:
            # Reconstruct time-series velocities across the window to find local peaks
            window_velocities = []
            for i in range(1, q_len):
                window_velocities.append(
                    abs(self.normalized_dists[i] - self.normalized_dists[i - 1])
                )

            # Count local peaks where velocity flips from increasing to decreasing
            # Enforce 0.005 threshold to filter out camera jitter
            for i in range(1, len(window_velocities) - 1):
                if (
                    window_velocities[i] > window_velocities[i - 1]
                    and window_velocities[i] > window_velocities[i + 1]
                    and window_velocities[i] > 0.005
                ):
                    peaks += 1

        # Calculate frequency in Hz (2 velocity peaks per full oscillation cycle)
        if q_len > 10:
            duration_seconds = q_len / self.fps
            frequency = (peaks / 2.0) / duration_seconds
        else:
            frequency = 0.0

        # Evaluate heuristic thresholds
        freq_match = self.freq_min <= frequency <= self.freq_max
        vel_match = mean_velocity > self.velocity_threshold
        std_match = std_dist > 0.01

        current_match = freq_match and vel_match and std_match

        # Track sustained duration for the "PENDING" status indicator
        elapsed_time = 0.0
        if current_match:
            current_time = time.time()
            if self.condition_met_start_time is None:
                self.condition_met_start_time = current_time
            self.consecutive_frames_met += 1
            elapsed_time = current_time - self.condition_met_start_time
        else:
            # Reset trackers
            self.condition_met_start_time = None
            self.consecutive_frames_met = 0

        # Append match outcome to the sliding activation window
        self.activation_window.append(1 if current_match else 0)

        # Calculate robust activation ratio
        activation_ratio = (
            sum(self.activation_window) / len(self.activation_window)
            if len(self.activation_window) > 0
            else 0.0
        )

        # Trigger stimming if activation ratio exceeds threshold within the short activation window
        min_frames = int(self.sustained_duration * self.fps)
        if (
            len(self.activation_window) >= min_frames
            and activation_ratio >= self.activation_ratio_threshold
        ):
            self.is_stimming = True
        else:
            self.is_stimming = False

        # Early warning for likely stimming before the full alert threshold is met
        self.is_likely_stimming = (
            len(self.activation_window) >= int(0.8 * min_frames) and activation_ratio >= 0.40
        )

        # Ear coverage heuristic evaluation
        if ear_coverage_dist < self.ear_coverage_threshold:
            self.ear_coverage_frames += 1
        else:
            self.ear_coverage_frames = 0
        self.ears_covered = self.ear_coverage_frames >= 30

        # Eyes closed heuristic evaluation
        if eye_openness_ratio < self.eye_closed_threshold:
            self.eye_closed_frames += 1
        else:
            self.eye_closed_frames = 0
        self.eyes_closed = self.eye_closed_frames > 45

        return {
            "normalized_dist": normalized_dist,
            "velocity": velocity,
            "mean_velocity": mean_velocity,
            "std_dist": std_dist,
            "frequency": frequency,
            "is_stimming": self.is_stimming,
            "is_likely_stimming": getattr(self, "is_likely_stimming", False),
            "elapsed_sustained": elapsed_time,
            "consecutive_frames": self.consecutive_frames_met,
            "activation_ratio": activation_ratio,
            "stimming_score": activation_ratio,
            "ears_covered": self.ears_covered,
            "eyes_closed": self.eyes_closed,
            "is_avoidance": self.ears_covered or self.eyes_closed,
            "ear_coverage_dist": ear_coverage_dist,
            "eye_openness_ratio": eye_openness_ratio,
        }

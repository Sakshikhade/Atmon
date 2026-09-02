import time
from math import sqrt


class EarsClosedStimmingDetector:
    """Detects sustained both-hands ear coverage with eyes closed.

    This module isolates the specialized rule for "ears-closed stimming" so that
    future tuning can remain focused in a single file.
    """

    def __init__(
        self, fps=30.0, hand_to_ear_threshold=0.18, required_duration=2.0, eye_closed_threshold=0.20
    ):
        self.fps = fps
        self.hand_to_ear_threshold = hand_to_ear_threshold
        self.eye_closed_threshold = eye_closed_threshold
        self.required_frames = max(1, int(required_duration * fps))

        self.both_hands_near_ears_frames = 0
        self.signal_start_time = None
        self.signal_active = False

    def _distance(self, a, b):
        return sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)

    def _normalized_distance(self, a, b, scale):
        if scale <= 1e-6:
            return float("inf")
        return self._distance(a, b) / scale

    def update(self, landmarks_data):
        pose_lm = landmarks_data.get("pose_landmarks")
        eye_openness_ratio = landmarks_data.get("eye_openness_ratio", 1.0)

        left_hand_near_ear = False
        right_hand_near_ear = False
        shoulder_width = None
        left_dist = float("inf")
        right_dist = float("inf")
        eyes_closed = eye_openness_ratio < self.eye_closed_threshold

        if pose_lm and len(pose_lm) > 16:
            l_shoulder = pose_lm[11]
            r_shoulder = pose_lm[12]
            l_ear = pose_lm[7]
            r_ear = pose_lm[8]
            l_wrist = pose_lm[15]
            r_wrist = pose_lm[16]

            shoulder_width = self._distance(l_shoulder, r_shoulder)
            left_dist = self._normalized_distance(l_wrist, l_ear, shoulder_width)
            right_dist = self._normalized_distance(r_wrist, r_ear, shoulder_width)

            left_hand_near_ear = left_dist < self.hand_to_ear_threshold
            right_hand_near_ear = right_dist < self.hand_to_ear_threshold

        both_hands_on_ears = left_hand_near_ear and right_hand_near_ear and eyes_closed

        if both_hands_on_ears:
            self.both_hands_near_ears_frames += 1
            if self.both_hands_near_ears_frames >= self.required_frames:
                if self.signal_start_time is None:
                    self.signal_start_time = time.time()
                self.signal_active = True
            else:
                self.signal_active = False
        else:
            self.both_hands_near_ears_frames = 0
            self.signal_active = False
            self.signal_start_time = None

        duration = 0.0
        if self.signal_active and self.signal_start_time is not None:
            duration = time.time() - self.signal_start_time

        behavior_action = "both_hands_on_ears" if both_hands_on_ears else "none"
        behavior_subcategory = "ears_closed_stimming" if self.signal_active else "none"
        behavior_category = "stimming" if self.signal_active else "none"

        return {
            "left_hand_near_ear": left_hand_near_ear,
            "right_hand_near_ear": right_hand_near_ear,
            "both_hands_on_ears": both_hands_on_ears,
            "eyes_closed": eyes_closed,
            "both_ears_closed_stimming": self.signal_active,
            "both_ears_closed_duration": duration,
            "left_hand_to_ear_dist": left_dist,
            "right_hand_to_ear_dist": right_dist,
            "shoulder_width": shoulder_width,
            "eye_openness_ratio": eye_openness_ratio,
            "required_frames": self.required_frames,
            "behavior_action": behavior_action,
            "behavior_subcategory": behavior_subcategory,
            "behavior_category": behavior_category,
        }

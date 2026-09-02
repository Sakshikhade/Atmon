from unittest.mock import MagicMock

from src.ears_closed_stimming import EarsClosedStimmingDetector


def make_landmarks(left_wrist_near_ear=True, right_wrist_near_ear=True):
    pose_lm = [MagicMock() for _ in range(33)]

    # Shoulders
    pose_lm[11].x, pose_lm[11].y, pose_lm[11].z = 0.4, 0.5, 0.0
    pose_lm[12].x, pose_lm[12].y, pose_lm[12].z = 0.6, 0.5, 0.0

    # Ears
    pose_lm[7].x, pose_lm[7].y, pose_lm[7].z = 0.38, 0.45, 0.0
    pose_lm[8].x, pose_lm[8].y, pose_lm[8].z = 0.62, 0.45, 0.0

    # Wrists
    if left_wrist_near_ear:
        pose_lm[15].x, pose_lm[15].y, pose_lm[15].z = 0.39, 0.46, 0.0
    else:
        pose_lm[15].x, pose_lm[15].y, pose_lm[15].z = 0.30, 0.70, 0.0

    if right_wrist_near_ear:
        pose_lm[16].x, pose_lm[16].y, pose_lm[16].z = 0.61, 0.46, 0.0
    else:
        pose_lm[16].x, pose_lm[16].y, pose_lm[16].z = 0.80, 0.70, 0.0

    return pose_lm


def test_both_ears_closed_stimming_requires_sustained_frames():
    detector = EarsClosedStimmingDetector(
        fps=30.0, required_duration=2.0, hand_to_ear_threshold=0.18
    )

    landmarks_data = {
        "pose_landmarks": make_landmarks(left_wrist_near_ear=True, right_wrist_near_ear=True),
        "eye_openness_ratio": 0.05,
    }

    result = None
    for frame_idx in range(60):
        result = detector.update(landmarks_data)
        if frame_idx < 59:
            assert result["both_ears_closed_stimming"] is False

    # On the 60th frame (2 seconds at 30 FPS), the signal becomes active.
    assert result["both_ears_closed_stimming"] is True
    assert result["both_hands_on_ears"] is True
    assert result["behavior_subcategory"] == "ears_closed_stimming"
    assert result["behavior_action"] == "both_hands_on_ears"

    # One additional frame should show a measurable active duration.
    result = detector.update(landmarks_data)
    assert result["both_ears_closed_duration"] > 0.0


def test_both_ears_closed_stimming_fails_when_one_hand_is_not_near_ear():
    detector = EarsClosedStimmingDetector(
        fps=30.0, required_duration=1.0, hand_to_ear_threshold=0.18
    )

    landmarks_data = {
        "pose_landmarks": make_landmarks(left_wrist_near_ear=True, right_wrist_near_ear=False),
        "eye_openness_ratio": 0.05,
    }

    for _ in range(35):
        result = detector.update(landmarks_data)

    assert result["both_ears_closed_stimming"] is False
    assert result["both_hands_on_ears"] is False


def test_both_ears_closed_stimming_fails_when_eyes_are_open():
    detector = EarsClosedStimmingDetector(
        fps=30.0, required_duration=1.0, hand_to_ear_threshold=0.18
    )

    landmarks_data = {
        "pose_landmarks": make_landmarks(left_wrist_near_ear=True, right_wrist_near_ear=True),
        "eye_openness_ratio": 0.40,
    }

    for _ in range(35):
        result = detector.update(landmarks_data)

    assert result["both_ears_closed_stimming"] is False
    assert result["eyes_closed"] is False

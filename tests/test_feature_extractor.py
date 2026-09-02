from unittest.mock import MagicMock

import pytest

from src.feature_extractor import SkeletalFeatureExtractor


def test_normalization_logic():
    # Mock landmarks
    # Case 1: Close to camera
    lm_close = [MagicMock() for _ in range(33)]
    # LEFT_SHOULDER (11)
    lm_close[11].x, lm_close[11].y, lm_close[11].z = 0.4, 0.4, 0.0
    # RIGHT_SHOULDER (12)
    lm_close[12].x, lm_close[12].y, lm_close[12].z = 0.6, 0.4, 0.0
    # LEFT_WRIST (15)
    lm_close[15].x, lm_close[15].y, lm_close[15].z = 0.4, 0.6, 0.0

    # Case 2: Far from camera (scaled down by 0.5)
    lm_far = [MagicMock() for _ in range(33)]
    lm_far[11].x, lm_far[11].y, lm_far[11].z = 0.45, 0.45, 0.0
    lm_far[12].x, lm_far[12].y, lm_far[12].z = 0.55, 0.45, 0.0
    lm_far[15].x, lm_far[15].y, lm_far[15].z = 0.45, 0.55, 0.0

    def get_norm_dist(pose_lm):
        l_shoulder = pose_lm[11]
        r_shoulder = pose_lm[12]
        l_wrist = pose_lm[15]

        dist_wrist_shoulder = (
            (l_wrist.x - l_shoulder.x) ** 2
            + (l_wrist.y - l_shoulder.y) ** 2
            + (l_wrist.z - l_shoulder.z) ** 2
        ) ** 0.5
        dist_shoulders = (
            (l_shoulder.x - r_shoulder.x) ** 2
            + (l_shoulder.y - r_shoulder.y) ** 2
            + (l_shoulder.z - r_shoulder.z) ** 2
        ) ** 0.5
        return dist_wrist_shoulder / dist_shoulders

    norm_close = get_norm_dist(lm_close)
    norm_far = get_norm_dist(lm_far)

    assert pytest.approx(norm_close) == 1.0
    assert pytest.approx(norm_far) == 1.0
    assert pytest.approx(norm_close) == norm_far


def _make_landmark(x=0.0, y=0.0, z=0.0):
    lm = MagicMock()
    lm.x, lm.y, lm.z = x, y, z
    return lm


def _build_extractor_with_landmarks(pose_lm, monkeypatch):
    """
    Construct a SkeletalFeatureExtractor without loading the MediaPipe model,
    wiring a mock detector that returns the supplied pose landmarks. The real
    ear-coverage / eye-openness math in extract_landmarks() then runs against
    these controlled coordinates.
    """
    import src.feature_extractor as fe

    # Build the instance without running __init__ (which needs the model file).
    extractor = SkeletalFeatureExtractor.__new__(SkeletalFeatureExtractor)

    detection_result = MagicMock()
    detection_result.pose_landmarks = [pose_lm]
    extractor.detector = MagicMock()
    extractor.detector.detect.return_value = detection_result

    # Neutralize the in-memory image conversion so we can pass a dummy frame.
    monkeypatch.setattr(fe.cv2, "cvtColor", lambda frame, code: frame)
    monkeypatch.setattr(fe.mp, "Image", lambda image_format, data: data)

    return extractor


def _base_pose_landmarks():
    """33 landmarks with shoulders and eyes placed at known coordinates."""
    pose_lm = [_make_landmark() for _ in range(33)]
    # Shoulders: 0.2 apart horizontally -> shoulder width = 0.2
    pose_lm[11] = _make_landmark(0.4, 0.5, 0.0)  # LEFT_SHOULDER
    pose_lm[12] = _make_landmark(0.6, 0.5, 0.0)  # RIGHT_SHOULDER
    # Ears
    pose_lm[7] = _make_landmark(0.38, 0.45, 0.0)  # LEFT_EAR
    pose_lm[8] = _make_landmark(0.62, 0.45, 0.0)  # RIGHT_EAR
    return pose_lm


def test_ear_coverage_distance_low_when_hands_near_ears(monkeypatch):
    # UT-005: wrists placed right next to the ears -> small normalized distance.
    pose_lm = _base_pose_landmarks()
    pose_lm[15] = _make_landmark(0.39, 0.46, 0.0)  # LEFT_WRIST near LEFT_EAR
    pose_lm[16] = _make_landmark(0.61, 0.46, 0.0)  # RIGHT_WRIST near RIGHT_EAR

    extractor = _build_extractor_with_landmarks(pose_lm, monkeypatch)
    result = extractor.extract_landmarks(frame=object())

    # Hands are close to ears: normalized ear-coverage distance well under the
    # 0.15 avoidance threshold used by the classifier.
    assert result["ear_coverage_dist"] < 0.15


def test_ear_coverage_distance_high_when_hands_away_from_ears(monkeypatch):
    # UT-005: wrists far from ears -> large normalized distance (no coverage).
    pose_lm = _base_pose_landmarks()
    pose_lm[15] = _make_landmark(0.30, 0.90, 0.0)  # LEFT_WRIST lowered
    pose_lm[16] = _make_landmark(0.70, 0.90, 0.0)  # RIGHT_WRIST lowered

    extractor = _build_extractor_with_landmarks(pose_lm, monkeypatch)
    result = extractor.extract_landmarks(frame=object())

    assert result["ear_coverage_dist"] > 0.15


def test_eye_openness_ratio_high_when_eyes_open(monkeypatch):
    # UT-006: noticeable vertical eyelid separation -> high openness ratio.
    pose_lm = _base_pose_landmarks()
    # Eyes ~0.2 apart horizontally (eye_dist), with the eye centre offset
    # vertically from the inner/outer midpoint to simulate an open eye.
    pose_lm[1] = _make_landmark(0.44, 0.40, 0.0)  # L_EYE_INNER
    pose_lm[2] = _make_landmark(0.42, 0.34, 0.0)  # L_EYE (centre, raised)
    pose_lm[3] = _make_landmark(0.40, 0.40, 0.0)  # L_EYE_OUTER
    pose_lm[4] = _make_landmark(0.56, 0.40, 0.0)  # R_EYE_INNER
    pose_lm[5] = _make_landmark(0.58, 0.34, 0.0)  # R_EYE (centre, raised)
    pose_lm[6] = _make_landmark(0.60, 0.40, 0.0)  # R_EYE_OUTER

    extractor = _build_extractor_with_landmarks(pose_lm, monkeypatch)
    result = extractor.extract_landmarks(frame=object())

    # Open eyes clamp toward the 1.0 ceiling; well above the 0.20 closed threshold.
    assert result["eye_openness_ratio"] > 0.20


def test_eye_openness_ratio_low_when_eyes_closed(monkeypatch):
    # UT-006: eye centre level with the inner/outer midpoint -> ~0 vertical gap.
    pose_lm = _base_pose_landmarks()
    pose_lm[1] = _make_landmark(0.44, 0.40, 0.0)  # L_EYE_INNER
    pose_lm[2] = _make_landmark(0.42, 0.40, 0.0)  # L_EYE (centre, flat)
    pose_lm[3] = _make_landmark(0.40, 0.40, 0.0)  # L_EYE_OUTER
    pose_lm[4] = _make_landmark(0.56, 0.40, 0.0)  # R_EYE_INNER
    pose_lm[5] = _make_landmark(0.58, 0.40, 0.0)  # R_EYE (centre, flat)
    pose_lm[6] = _make_landmark(0.60, 0.40, 0.0)  # R_EYE_OUTER

    extractor = _build_extractor_with_landmarks(pose_lm, monkeypatch)
    result = extractor.extract_landmarks(frame=object())

    # Closed eyes: near-zero vertical separation -> ratio below the 0.20 threshold.
    assert result["eye_openness_ratio"] < 0.20


def test_simulation_overrides_force_avoidance_values(monkeypatch):
    # Simulation flags must override computed values (used by the HUD test keys).
    pose_lm = _base_pose_landmarks()
    pose_lm[15] = _make_landmark(0.30, 0.90, 0.0)
    pose_lm[16] = _make_landmark(0.70, 0.90, 0.0)

    extractor = _build_extractor_with_landmarks(pose_lm, monkeypatch)
    result = extractor.extract_landmarks(
        frame=object(),
        simulated_ears_covered=True,
        simulated_eyes_closed=True,
    )

    assert result["ear_coverage_dist"] == 0.08
    assert result["eye_openness_ratio"] == 0.05

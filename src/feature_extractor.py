import os
from collections import deque

import cv2
import mediapipe as mp
from mediapipe.tasks import python
from mediapipe.tasks.python import vision

from src.logging_config import get_logger

logger = get_logger(__name__)

# Define full 33-point pose landmark connections for custom rendering
POSE_CONNECTIONS = [
    # Face / Head
    (0, 1),
    (1, 2),
    (2, 3),
    (3, 7),
    (0, 4),
    (4, 5),
    (5, 6),
    (6, 8),
    (9, 10),
    # Torso
    (11, 12),
    (11, 23),
    (12, 24),
    (23, 24),
    # Left Arm
    (11, 13),
    (13, 15),
    (15, 17),
    (15, 19),
    (15, 21),
    (17, 19),
    # Right Arm
    (12, 14),
    (14, 16),
    (16, 18),
    (16, 20),
    (16, 22),
    (18, 20),
    # Left Leg
    (23, 25),
    (25, 27),
    (27, 29),
    (27, 31),
    (29, 31),
    # Right Leg
    (24, 26),
    (26, 28),
    (28, 30),
    (28, 32),
    (30, 32),
]


class SkeletalFeatureExtractor:
    """
    SkeletalFeatureExtractor extracts 3D skeletal landmarks from video frames using MediaPipe Tasks API.
    It strictly adheres to Privacy-by-Design principles:
    - Raw frames are processed purely in-memory.
    - No video or image data is persisted to disk or transmitted — ever.
    - Raw frames are discarded immediately after keypoint extraction.

    Normalized landmark *coordinates* (numeric x/y/z values) for resolved behavior
    episodes are stored separately in ``episode_snapshots`` (Sprint 16a) as stick-figure
    replay data. This is clearly distinct from imagery: no pixel data is involved, and
    the coordinates are stored only for resolved episodes, not continuously.
    """

    def __init__(
        self, min_detection_confidence=0.5, min_presence_confidence=0.5, min_tracking_confidence=0.5
    ):
        # Resolve model path relative to this script
        script_dir = os.path.dirname(os.path.abspath(__file__))
        model_path = os.path.join(script_dir, "../pose_landmarker.task")

        if not os.path.exists(model_path):
            raise FileNotFoundError(
                f"Pose landmarker model not found at {model_path}.\n"
                f"Please ensure pose_landmarker.task is in the root directory."
            )

        # Initialize the newer MediaPipe Tasks PoseLandmarker
        base_options = python.BaseOptions(model_asset_path=model_path)
        options = vision.PoseLandmarkerOptions(
            base_options=base_options,
            running_mode=vision.RunningMode.IMAGE,
            min_pose_detection_confidence=min_detection_confidence,
            min_pose_presence_confidence=min_presence_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self.detector = vision.PoseLandmarker.create_from_options(options)

        # Sliding time-series window (max 60 frames) for normalization tracking
        self.rolling_window = deque(maxlen=60)

    def extract_landmarks(self, frame, simulated_ears_covered=False, simulated_eyes_closed=False):
        """
        Extracts 3D landmarks for pose from a BGR image frame.
        The input frame is processed entirely in-memory.

        Parameters:
            frame (np.ndarray): The raw BGR frame from the video capture.
            simulated_ears_covered (bool): If True, overrides ear coverage distance to trigger avoidance.
            simulated_eyes_closed (bool): If True, overrides eye openness ratio to trigger avoidance.

        Returns:
            dict: A dictionary containing extracted pose landmarks, ear coverage distance, and eye openness ratio.
        """
        # Convert BGR frame to RGB for MediaPipe processing
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        # Create MediaPipe Image object
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)

        # Process pose detection in-memory
        detection_result = self.detector.detect(mp_image)

        # Discard intermediate image memory immediately to keep footprint low
        del rgb_frame
        del mp_image

        extracted_data = {
            "pose_landmarks": None,
            "ear_coverage_dist": 999.0,
            "eye_openness_ratio": 1.0,
        }

        # Extract Pose Landmarks (MediaPipe Tasks returns list of lists)
        if detection_result.pose_landmarks and len(detection_result.pose_landmarks) > 0:
            pose_lm = detection_result.pose_landmarks[0]
            extracted_data["pose_landmarks"] = pose_lm

            # Ensure index safety (PoseLandmarker has 33 landmarks)
            if len(pose_lm) > 16:
                l_shoulder = pose_lm[11]
                r_shoulder = pose_lm[12]
                l_ear = pose_lm[7]
                r_ear = pose_lm[8]
                l_wrist = pose_lm[15]
                r_wrist = pose_lm[16]

                # Check for eyes landmarks
                l_eye_inner = pose_lm[1]
                l_eye = pose_lm[2]
                l_eye_outer = pose_lm[3]
                r_eye_inner = pose_lm[4]
                r_eye = pose_lm[5]
                r_eye_outer = pose_lm[6]

                # Shoulder width normalization factor
                dist_shoulders = (
                    (l_shoulder.x - r_shoulder.x) ** 2
                    + (l_shoulder.y - r_shoulder.y) ** 2
                    + (l_shoulder.z - r_shoulder.z) ** 2
                ) ** 0.5

                if dist_shoulders > 0.0001:
                    # Normalized Wrist to Ear distances
                    dist_l_wrist_ear = (
                        (
                            (l_wrist.x - l_ear.x) ** 2
                            + (l_wrist.y - l_ear.y) ** 2
                            + (l_wrist.z - l_ear.z) ** 2
                        )
                        ** 0.5
                    ) / dist_shoulders
                    dist_r_wrist_ear = (
                        (
                            (r_wrist.x - r_ear.x) ** 2
                            + (r_wrist.y - r_ear.y) ** 2
                            + (r_wrist.z - r_ear.z) ** 2
                        )
                        ** 0.5
                    ) / dist_shoulders
                    extracted_data["ear_coverage_dist"] = min(dist_l_wrist_ear, dist_r_wrist_ear)
                else:
                    extracted_data["ear_coverage_dist"] = 999.0

                # Eye Openness Ratio (scaled vertical distance relative to eye-to-eye horizontal distance)
                eye_dist = ((l_eye.x - r_eye.x) ** 2 + (l_eye.y - r_eye.y) ** 2) ** 0.5
                if eye_dist > 0.0001:
                    left_val = abs(l_eye.y - (l_eye_inner.y + l_eye_outer.y) / 2.0)
                    right_val = abs(r_eye.y - (r_eye_inner.y + r_eye_outer.y) / 2.0)
                    base_ratio = (left_val + right_val) / (2.0 * eye_dist)
                    extracted_data["eye_openness_ratio"] = min(base_ratio * 15.0, 1.0)
                else:
                    extracted_data["eye_openness_ratio"] = 1.0

        # Apply simulation overrides if set
        if simulated_ears_covered:
            extracted_data["ear_coverage_dist"] = 0.08
        if simulated_eyes_closed:
            extracted_data["eye_openness_ratio"] = 0.05

        # Explicitly release detection result to free underlying C++ memory structures
        del detection_result

        return extracted_data

    def draw_landmarks(self, frame, landmarks_data):
        """
        Draws custom high-contrast neon skeletal overlays directly onto a BGR frame.
        """
        pose_lm = landmarks_data.get("pose_landmarks")
        if not pose_lm:
            return frame

        h, w, _ = frame.shape

        # Draw all skeletal connection lines in bright neon green
        for p1_idx, p2_idx in POSE_CONNECTIONS:
            if p1_idx < len(pose_lm) and p2_idx < len(pose_lm):
                p1 = pose_lm[p1_idx]
                p2 = pose_lm[p2_idx]

                # Only draw connections if both landmarks meet reasonable visibility thresholds
                if p1.visibility > 0.5 and p2.visibility > 0.5:
                    pt1 = (int(p1.x * w), int(p1.y * h))
                    pt2 = (int(p2.x * w), int(p2.y * h))
                    cv2.line(frame, pt1, pt2, (0, 255, 0), 2)

        # Draw individual joints
        for idx, lm in enumerate(pose_lm):
            if lm.visibility > 0.5:
                cx, cy = int(lm.x * w), int(lm.y * h)

                # Highlight the 4 target landmarks (Shoulders 11, 12 and Wrists 15, 16) in red
                if idx in [11, 12, 15, 16]:
                    cv2.circle(frame, (cx, cy), 6, (0, 0, 255), -1)  # Red core
                    cv2.circle(frame, (cx, cy), 8, (255, 255, 255), 1)  # White border
                else:
                    cv2.circle(frame, (cx, cy), 3, (255, 255, 0), -1)  # Neon cyan/yellow core

        return frame

    def release(self):
        """
        Release the MediaPipe model resources.
        """
        self.detector.close()


def main():
    # Initialize extractor with 0.5 detection confidence
    try:
        extractor = SkeletalFeatureExtractor(
            min_detection_confidence=0.5, min_tracking_confidence=0.5
        )
    except Exception as e:
        logger.error("Error initializing SkeletalFeatureExtractor: %s", e)
        return

    # Open video capture loop using device 0 (default webcam)
    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        logger.error("Could not open webcam.")
        return

    logger.info("AAMAS Skeletal Landmark Feature Extractor active (MediaPipe Tasks API)")
    logger.info("Privacy mode: processing strictly in-memory, zero disk persistence")
    logger.info("Press 'q' in the window to quit")

    while True:
        ret, frame = cap.read()
        if not ret:
            logger.error("Failed to grab frame.")
            break

        # Extract skeletal landmarks in-memory
        landmarks_data = extractor.extract_landmarks(frame)
        pose_lm = landmarks_data.get("pose_landmarks")

        if pose_lm:
            # Extract key landmarks
            # 11: LEFT_SHOULDER, 12: RIGHT_SHOULDER, 15: LEFT_WRIST, 16: RIGHT_WRIST
            l_shoulder = pose_lm[11]
            r_shoulder = pose_lm[12]
            l_wrist = pose_lm[15]

            # Compute 3D Euclidean distances
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

            # Normalize wrist-to-shoulder distance by shoulder-to-shoulder width
            if dist_shoulders > 0:
                normalized_dist = dist_wrist_shoulder / dist_shoulders
            else:
                normalized_dist = 0.0

            # Calculate velocity (absolute change compared to previous frame's normalized distance)
            if len(extractor.rolling_window) >= 1:
                prev_dist = extractor.rolling_window[-1]
                velocity = abs(normalized_dist - prev_dist)
            else:
                velocity = 0.0

            # Append current normalized distance to the sliding time-series rolling window queue
            extractor.rolling_window.append(normalized_dist)

            # Analyze the sliding window history to count velocity peaks (rhythm checker)
            peaks = 0
            q_len = len(extractor.rolling_window)
            if q_len >= 3:
                # Reconstruct time-series velocities across the window
                window_velocities = []
                for i in range(1, q_len):
                    window_velocities.append(
                        abs(extractor.rolling_window[i] - extractor.rolling_window[i - 1])
                    )

                # Count local peaks where velocity flips from increasing to decreasing
                # Enforce a tiny threshold of 0.005 to filter out minor camera jitter and noise
                for i in range(1, len(window_velocities) - 1):
                    if (
                        window_velocities[i] > window_velocities[i - 1]
                        and window_velocities[i] > window_velocities[i + 1]
                        and window_velocities[i] > 0.005
                    ):
                        peaks += 1

            # Convert peak count to approximate motion Frequency (Hz) based on ~30 FPS
            # There are 2 velocity peaks per full oscillation cycle, so cycles = peaks / 2.0
            if q_len > 10:
                duration_seconds = q_len / 30.0
                frequency = (peaks / 2.0) / duration_seconds
            else:
                frequency = 0.0

            # Print HUD stats on a single dynamically updating line using carriage return
            print(
                f"\rQueue Size: {q_len:>2}/60 | "
                f"Norm Dist: {normalized_dist:.3f} | "
                f"Velocity: {velocity:.3f} | "
                f"Frequency: {frequency:.2f} Hz",
                end="",
                flush=True,
            )

        # Draw the visual overlays (skeletal connections)
        frame = extractor.draw_landmarks(frame, landmarks_data)

        # Display the frame
        cv2.imshow("AAMAS - Skeletal Tracking", frame)

        # Enforce 'Privacy-by-Design' by explicitly discarding/deleting the local BGR frame
        del frame

        # Gracefully break loop when 'q' is pressed in the OpenCV window
        if cv2.waitKey(1) & 0xFF == ord("q"):
            break

    # Clean up and release webcam resources
    cap.release()
    extractor.release()
    cv2.destroyAllWindows()
    logger.info("Webcam released and system shutdown gracefully.")


if __name__ == "__main__":
    main()

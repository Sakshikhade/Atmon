import argparse
import datetime
import os
import sys
import time
from collections import deque

import cv2

# Ensure src is in python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.alert_gateway import AlertGateway
from src.camera_stream import ThreadedCameraStream
from src.data_logger import DataLogger
from src.env_config import load_environment, print_env_banner
from src.event_storage import EventStorage, build_snapshot, utcnow_iso
from src.logging_config import configure_logging, get_logger
from src.pipeline import EventTracker, build_detectors, compute_normalized_distance

logger = get_logger(__name__)


def main():
    # Parse command line arguments
    def parse_video_source(value):
        try:
            return int(value)
        except ValueError:
            return value

    parser = argparse.ArgumentParser(
        description="AAMAS - Autism Activity Monitoring & Alerting System"
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["heuristic", "ml"],
        default="heuristic",
        help="Classification mode: 'heuristic' (rule-based) or 'ml' (Random Forest)",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default="models/stimming_classifier.pkl",
        help="Path to serialized scikit-learn classifier model",
    )
    parser.add_argument(
        "--source",
        type=parse_video_source,
        default=0,
        help="Camera device index or path to a video file (e.g. samples/sample-2.mp4)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/outbox.db",
        help="Path to local SQLite database for event storage (default: data/outbox.db)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run without a display window (useful for headless environments)",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()
    args.headless = args.headless or not os.environ.get("DISPLAY")
    configure_logging(args.log_level)

    # Resolve + load the environment and show a banner.
    env = load_environment()
    print_env_banner(env)

    if args.headless:
        logger.info("Running in headless mode; no display window will be shown.")

    if args.mode == "ml":
        logger.info("Operating in Machine Learning mode (using %s)", args.model_path)
    else:
        logger.info("Operating in rule-based Heuristic mode")

    # Build the shared detection stack (same config the ingest CLI uses).
    try:
        extractor, classifier, ears_closed_detector = build_detectors(
            mode=args.mode, model_path=args.model_path, fps=30.0
        )
    except Exception as e:
        logger.error("Error initializing detectors: %s", e)
        return

    # Initialize DataLogger
    data_logger = DataLogger()
    frame_index = 0

    # Initialize Alert Gateway and State Trackers
    gateway = AlertGateway()

    # Shared edge-triggered state machine for stimming/avoidance transitions
    # (same EventTracker the ingest CLI uses — replaces the old inline was_*/start_time bookkeeping).
    event_tracker = EventTracker()
    key = 0xFF  # Default key value for keyboard simulation

    # Initialize EventStorage for local episode persistence
    storage = EventStorage(db_path=args.db_path)
    landmark_buffer = deque(maxlen=60)

    # Performance and latency profiling buffers (rolling 100 frames)
    mp_latencies = deque(maxlen=100)
    clf_latencies = deque(maxlen=100)
    total_latencies = deque(maxlen=100)
    frame_times = deque(maxlen=100)

    # Open thread-safe non-blocking camera stream
    stream = ThreadedCameraStream(src=args.source)
    if not stream.grabbed:
        logger.error("Could not open video source: %s", args.source)
        extractor.release()
        stream.release()
        return

    # Set camera resolution to standard for consistency and speed (640x480 is fast and reliable)
    stream.stream.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    stream.stream.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

    # Start background ingestion thread
    stream.start()

    logger.info("AAMAS Core Monitor active (MediaPipe Tasks API)")
    logger.info("Thresholds: freq 3.0-6.5 Hz, avg velocity > 0.04, sustained >= 1.5s (~45 frames)")
    logger.info("Privacy mode: strict local in-memory frame erasure active")
    logger.info("Press 'q' in the window to quit")

    classification = None
    no_detection_counter = 0
    loop_fps = 30.0

    while True:
        ret, frame = stream.read()
        if not ret:
            logger.error("Camera stream stopped.")
            break
        if frame is None:
            # Yield CPU briefly and retry in next iteration
            time.sleep(0.001)
            continue

        frame_start = time.time()
        frame_times.append(frame_start)

        h, w, _ = frame.shape

        # Determine simulation flags
        sim_ears = (key == ord("c")) or (key == ord("a"))
        sim_eyes = (key == ord("e")) or (key == ord("a"))

        # Extract skeletal landmarks in-memory
        t0 = time.time()
        landmarks_data = extractor.extract_landmarks(
            frame, simulated_ears_covered=sim_ears, simulated_eyes_closed=sim_eyes
        )
        t1 = time.time()
        mp_latencies.append((t1 - t0) * 1000.0)

        ears_closed_signal = ears_closed_detector.update(landmarks_data)
        pose_lm = landmarks_data.get("pose_landmarks")

        if pose_lm:
            no_detection_counter = 0
            landmark_buffer.append((time.time(), pose_lm))
            # Scale-invariant wrist-to-shoulder distance (max of both arms),
            # normalized by shoulder width. Shared with the ingest CLI via src/pipeline.
            normalized_dist = compute_normalized_distance(pose_lm)

            # Feed features into the classifier
            t2 = time.time()
            # Update classifier with dynamic FPS if possible
            if hasattr(classifier, "fps") and loop_fps > 0:
                classifier.fps = loop_fps
            classification = classifier.update(
                normalized_dist,
                ear_coverage_dist=landmarks_data.get("ear_coverage_dist", 999.0),
                eye_openness_ratio=landmarks_data.get("eye_openness_ratio", 1.0),
            )

            if ears_closed_signal["both_ears_closed_stimming"]:
                classification["ears_closed_stimming"] = True
                classification["eyes_closed"] = True
                classification["is_stimming"] = True
                classification["is_avoidance"] = False
                classification["behavior_category"] = "stimming"
                classification["behavior_subcategory"] = "ears_closed_stimming"
                classification["behavior_action"] = "both_hands_on_ears"
            else:
                classification["ears_closed_stimming"] = False
                if classification["is_stimming"]:
                    classification["behavior_category"] = "stimming"
                    classification["behavior_subcategory"] = "rhythmic_stimming"
                    classification["behavior_action"] = "repetitive_wrist_motion"
                elif classification.get("is_avoidance", False):
                    classification["behavior_category"] = "avoidance"
                    if classification.get("ears_covered"):
                        classification["behavior_subcategory"] = "ears_covered_avoidance"
                        classification["behavior_action"] = "hand_to_ear"
                    elif classification.get("eyes_closed"):
                        classification["behavior_subcategory"] = "eyes_closed_avoidance"
                        classification["behavior_action"] = "eyes_closed"
                    else:
                        classification["behavior_subcategory"] = "avoidance"
                        classification["behavior_action"] = "avoidance"
                else:
                    classification["behavior_category"] = "normal"
                    classification["behavior_subcategory"] = "none"
                    classification["behavior_action"] = "none"

            t3 = time.time()
            clf_latencies.append((t3 - t2) * 1000.0)
        else:
            clf_latencies.append(0.0)
            no_detection_counter += 1
            # If tracking is lost for more than 10 consecutive frames, safety-reset the stimming timers
            if no_detection_counter > 10:
                classifier.reset_tracking()
                if classification:
                    classification["is_stimming"] = False
                    classification["is_avoidance"] = False
                    classification["ears_covered"] = False
                    classification["eyes_closed"] = False
                    classification["elapsed_sustained"] = 0.0
                    classification["consecutive_frames"] = 0

        # Track State Transitions for Alert Gateway Webhooks
        current_stimming = classification["is_stimming"] if classification else False
        current_ears_closed_stimming = (
            classification["ears_closed_stimming"]
            if classification and "ears_closed_stimming" in classification
            else False
        )
        current_ears_covered = (
            classification["ears_covered"]
            if classification
            and "ears_covered" in classification
            and not current_ears_closed_stimming
            else False
        )
        current_eyes_closed = (
            classification["eyes_closed"]
            if classification
            and "eyes_closed" in classification
            and not current_ears_closed_stimming
            else False
        )

        # Feed the shared EventTracker; it owns the start/resolved edge detection and
        # duration bookkeeping (same state machine the ingest CLI uses). We dispatch
        # the live-only side-effects (freq/velocity webhook payloads + outbox record)
        # from the emitted transitions, preserving the exact prior behavior.
        _freq = classification.get("frequency", 0.0) if classification else 0.0
        _vel = classification.get("mean_velocity", 0.0) if classification else 0.0

        for ev in event_tracker.update(
            stimming=current_stimming,
            ears_covered=current_ears_covered,
            eyes_closed=current_eyes_closed,
        ):
            if ev.behavior == "stimming":
                if ev.phase == "start":
                    gateway.send_alert(
                        event_status="stimming_start",
                        frequency=_freq,
                        mean_velocity=_vel,
                        duration=classification.get("elapsed_sustained", 0.0)
                        if classification
                        else 0.0,
                        event_type=classification.get("behavior_subcategory", "stimming")
                        if classification
                        else "stimming",
                        detail=classification.get("behavior_action", "stimming")
                        if classification
                        else "stimming",
                    )
                else:  # resolved
                    gateway.send_alert(
                        event_status="stimming_resolved",
                        frequency=_freq,
                        mean_velocity=_vel,
                        duration=ev.duration_s,
                        event_type=classification.get("behavior_subcategory", "stimming")
                        if classification
                        else "stimming",
                        detail=classification.get("behavior_action", "stimming")
                        if classification
                        else "stimming",
                    )
            else:  # ears_covered / eyes_closed -> avoidance
                detail = ev.behavior  # "ears_covered" | "eyes_closed"
                if ev.phase == "start":
                    gateway.send_alert(
                        event_status="avoidance_start",
                        frequency=_freq,
                        mean_velocity=_vel,
                        duration=0.0,
                        event_type="avoidance_start",
                        detail=detail,
                    )
                else:  # resolved
                    gateway.send_alert(
                        event_status="avoidance_resolved",
                        frequency=_freq,
                        mean_velocity=_vel,
                        duration=ev.duration_s,
                        event_type="avoidance_resolved",
                        detail=detail,
                    )

            if ev.phase == "resolved":
                snapshot = build_snapshot(
                    landmark_buffer,
                    started_at=ev.started_at,
                    ended_at=ev.ended_at,
                )
                start_iso = (
                    datetime.datetime.fromtimestamp(ev.started_at, datetime.UTC).isoformat()
                    if ev.started_at
                    else utcnow_iso()
                )
                end_iso = (
                    datetime.datetime.fromtimestamp(ev.ended_at, datetime.UTC).isoformat()
                    if ev.ended_at
                    else utcnow_iso()
                )
                storage.add_event(
                    type=ev.event_type,
                    subtype=ev.subtype,
                    action=ev.action,
                    started_at=start_iso,
                    ended_at=end_iso,
                    duration_s=round(ev.duration_s, 2),
                    intensity=round(_vel, 3) if _vel > 0 else None,
                    source="live",
                    snapshot=snapshot,
                )
                logger.info(
                    "Persisted live event [%s/%s] duration=%.2fs (snapshot=%s)",
                    ev.event_type,
                    ev.subtype,
                    ev.duration_s,
                    "yes" if snapshot else "no",
                )

        # Draw the visual skeletal overlays (neon green connections)
        frame = extractor.draw_landmarks(frame, landmarks_data)

        # Record total loop processing time (excluding display refresh / waitKey)
        frame_end = time.time()
        total_latencies.append((frame_end - frame_start) * 1000.0)

        # Compute rolling averages for profiling
        avg_mp = sum(mp_latencies) / len(mp_latencies) if mp_latencies else 0.0
        avg_clf = sum(clf_latencies) / len(clf_latencies) if clf_latencies else 0.0
        avg_total = sum(total_latencies) / len(total_latencies) if total_latencies else 0.0
        loop_fps = 0.0
        if len(frame_times) > 1:
            time_delta = frame_times[-1] - frame_times[0]
            if time_delta > 0:
                loop_fps = (len(frame_times) - 1) / time_delta

        # ----------------- DRAW HIGH-CONTRAST NEON HUD -----------------
        # 1. Semi-transparent black background panel for HUD stats
        overlay = frame.copy()
        panel_x1, panel_y1 = 15, 75
        panel_x2, panel_y2 = 280, 420
        cv2.rectangle(overlay, (panel_x1, panel_y1), (panel_x2, panel_y2), (0, 0, 0), -1)
        cv2.addWeighted(overlay, 0.65, frame, 0.35, 0, frame)

        # Neon cyan border for the stats panel
        cv2.rectangle(frame, (panel_x1, panel_y1), (panel_x2, panel_y2), (255, 255, 0), 1)

        # Add text labels
        cv2.putText(
            frame,
            "AAMAS CORE MONITOR v1.0",
            (panel_x1 + 10, panel_y1 + 20),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (255, 255, 255),
            1,
            cv2.LINE_AA,
        )

        # Determine status colors and values
        status_text = "NORMAL"
        status_color = (0, 255, 0)  # Neon green

        freq_val = 0.0
        avg_vel_val = 0.0
        std_dist_val = 0.0
        elapsed_val = 0.0
        ear_coverage_val = 999.0
        eye_openness_val = 1.0
        q_len = len(classifier.normalized_dists)

        act_ratio_val = 0.0
        stimming_score_val = 0.0
        if classification:
            freq_val = classification["frequency"]
            avg_vel_val = classification["mean_velocity"]
            std_dist_val = classification["std_dist"]
            elapsed_val = classification["elapsed_sustained"]
            ear_coverage_val = classification.get("ear_coverage_dist", 999.0)
            eye_openness_val = classification.get("eye_openness_ratio", 1.0)
            act_ratio_val = classification.get("activation_ratio", 0.0)
            stimming_score_val = classification.get("stimming_score", 0.0)

            if classification.get("ears_closed_stimming", False):
                status_text = "EARS-CLOSED STIMMING"
                status_color = (0, 220, 255)  # Warm Yellow/Orange for stimming
            elif classification["is_stimming"]:
                status_text = "STIMMING"
                status_color = (0, 220, 255)  # Warm Yellow/Orange for stimming
            elif classification.get("is_likely_stimming", False):
                status_text = "POSSIBLE STIMMING"
                status_color = (0, 165, 255)  # Orange for likely stimming
            elif classification.get("is_avoidance", False):
                status_text = "AVOIDANCE"
                status_color = (0, 120, 255)  # Amber for avoidance
            elif classification["consecutive_frames"] > 0:
                status_text = f"PENDING ({elapsed_val:.1f}s)"
                status_color = (0, 165, 255)  # Orange

        # Render real-time stats
        cv2.putText(
            frame,
            f"STATUS: {status_text}",
            (panel_x1 + 10, panel_y1 + 45),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            status_color,
            2,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"SUBTYPE: {classification.get('behavior_subcategory', 'none').replace('_', ' ').upper()}",
            (panel_x1 + 10, panel_y1 + 70),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"ACTION: {classification.get('behavior_action', 'none').replace('_', ' ').upper()}",
            (panel_x1 + 10, panel_y1 + 90),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (220, 220, 220),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Queue Size: {q_len}/60",
            (panel_x1 + 10, panel_y1 + 110),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Peak Freq: {freq_val:.2f} Hz",
            (panel_x1 + 10, panel_y1 + 130),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 0) if (3.0 <= freq_val <= 6.5) else (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Avg Velocity: {avg_vel_val:.4f}",
            (panel_x1 + 10, panel_y1 + 150),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 0) if (avg_vel_val > 0.04) else (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Distance Std: {std_dist_val:.4f}",
            (panel_x1 + 10, panel_y1 + 170),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Act. Ratio: {act_ratio_val * 100:.1f}%",
            (panel_x1 + 10, panel_y1 + 190),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Stimming Score: {stimming_score_val:.2f}",
            (panel_x1 + 10, panel_y1 + 210),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (255, 255, 0) if stimming_score_val > 0.3 else (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Ear Cover Dist: {ear_coverage_val:.4f}",
            (panel_x1 + 10, panel_y1 + 230),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 120, 255) if (ear_coverage_val < 0.15) else (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Eye Openness: {eye_openness_val:.4f}",
            (panel_x1 + 10, panel_y1 + 250),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 120, 255) if (eye_openness_val < 0.20) else (200, 200, 200),
            1,
            cv2.LINE_AA,
        )

        # Performance profiling overlay
        cv2.putText(
            frame,
            f"Loop FPS: {loop_fps:.1f}",
            (panel_x1 + 10, panel_y1 + 275),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 255),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"MP Latency: {avg_mp:.1f} ms",
            (panel_x1 + 10, panel_y1 + 295),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Clf Latency: {avg_clf:.1f} ms",
            (panel_x1 + 10, panel_y1 + 315),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (200, 200, 200),
            1,
            cv2.LINE_AA,
        )
        cv2.putText(
            frame,
            f"Total CPU: {avg_total:.1f} ms",
            (panel_x1 + 10, panel_y1 + 335),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (0, 255, 0) if (avg_total <= 33.3) else (0, 0, 255),
            1,
            cv2.LINE_AA,
        )

        # 2. Draw Alert Overlay Alert Banner and Frame Border
        if classification and classification["is_stimming"]:
            # Thick yellow border around the camera frame
            border_thickness = 8
            cv2.rectangle(frame, (0, 0), (w, h), (0, 220, 255), border_thickness)

            # Translucent top bar for alert banner
            banner_overlay = frame.copy()
            cv2.rectangle(banner_overlay, (0, 0), (w, 55), (0, 220, 255), -1)
            # Alpha blend banner overlay to show camera behind it
            cv2.addWeighted(banner_overlay, 0.85, frame, 0.15, 0, frame)

            # High-contrast alert text centered on the yellow banner
            alert_text = "[!] STIMMING EVENT DETECTED"
            text_size = cv2.getTextSize(alert_text, cv2.FONT_HERSHEY_DUPLEX, 0.75, 2)[0]
            text_x = (w - text_size[0]) // 2
            text_y = (55 + text_size[1]) // 2
            cv2.putText(
                frame,
                alert_text,
                (text_x, text_y),
                cv2.FONT_HERSHEY_DUPLEX,
                0.75,
                (0, 0, 0),
                2,
                cv2.LINE_AA,
            )

            # Print to stdout with carriage return to keep console clean
            print(
                f"\r[!] ALERT: STIMMING DETECTED | Freq: {freq_val:.2f} Hz | Loop FPS: {loop_fps:.1f} | MP: {avg_mp:.1f}ms | Clf: {avg_clf:.1f}ms  ",
                end="",
                flush=True,
            )
        elif classification and classification.get("is_avoidance", False):
            # Thick amber border around the camera frame
            border_thickness = 8
            cv2.rectangle(frame, (0, 0), (w, h), (0, 120, 255), border_thickness)

            # Translucent top bar for alert banner
            banner_overlay = frame.copy()
            cv2.rectangle(banner_overlay, (0, 0), (w, 55), (0, 120, 255), -1)
            cv2.addWeighted(banner_overlay, 0.85, frame, 0.15, 0, frame)

            avoidance_detail = ""
            if classification.get("ears_closed_stimming"):
                avoidance_detail = "EARS-CLOSED STIMMING"
            elif classification.get("ears_covered"):
                avoidance_detail = "EARS COVERED"
            elif classification.get("eyes_closed"):
                avoidance_detail = "EYES CLOSED"
            else:
                avoidance_detail = "ACTIVE"

            alert_text = f"[!] AVOIDANCE DETECTED: {avoidance_detail}"
            text_size = cv2.getTextSize(alert_text, cv2.FONT_HERSHEY_DUPLEX, 0.75, 2)[0]
            text_x = (w - text_size[0]) // 2
            text_y = (55 + text_size[1]) // 2
            cv2.putText(
                frame,
                alert_text,
                (text_x, text_y),
                cv2.FONT_HERSHEY_DUPLEX,
                0.75,
                (0, 0, 0),
                2,
                cv2.LINE_AA,
            )

            # Print to stdout with carriage return to keep console clean
            print(
                f"\r[!] ALERT: AVOIDANCE DETECTED ({avoidance_detail}) | Loop FPS: {loop_fps:.1f} | MP: {avg_mp:.1f}ms | Clf: {avg_clf:.1f}ms  ",
                end="",
                flush=True,
            )
        elif pose_lm:
            # Print standard monitor logs
            print(
                f"\rMonitoring | Freq: {freq_val:.2f} Hz | Loop FPS: {loop_fps:.1f} | MP: {avg_mp:.1f}ms | Clf: {avg_clf:.1f}ms | Status: {status_text}  ",
                end="",
                flush=True,
            )
        else:
            print(
                f"\rMonitoring | [No Skeleton Detected] | Loop FPS: {loop_fps:.1f} | MP: {avg_mp:.1f}ms | Clf: {avg_clf:.1f}ms  ",
                end="",
                flush=True,
            )

        # Check keyboard inputs for logging or exit
        recording_label = None
        if not args.headless:
            key = cv2.waitKey(1) & 0xFF
            if key == ord("n"):
                recording_label = 0
            elif key == ord("s"):
                recording_label = 1
            elif key == ord("a"):
                recording_label = 2
            elif key == ord("q"):
                break
        else:
            key = -1

        if recording_label is not None and pose_lm and classification:
            data_logger.log_frame(
                frame_index=frame_index,
                mean_dist=classification["normalized_dist"],
                std_dist=classification["std_dist"],
                mean_vel=classification["mean_velocity"],
                peak_freq=classification["frequency"],
                mean_ear_coverage=classification.get("ear_coverage_dist", 999.0),
                mean_eye_openness=classification.get("eye_openness_ratio", 1.0),
                label=recording_label,
            )
            # Draw recording indicator badge
            cv2.circle(frame, (w - 130, 30), 7, (0, 0, 255), -1)
            if recording_label == 0:
                rec_str = "REC: NORMAL"
            elif recording_label == 1:
                rec_str = "REC: STIMMING"
            else:
                rec_str = "REC: AVOIDANCE"
            cv2.putText(
                frame,
                rec_str,
                (w - 115, 35),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.45,
                (0, 0, 255),
                1,
                cv2.LINE_AA,
            )
            print(f" [Logged: {rec_str[5:]}]", end="", flush=True)

        frame_index += 1

        if not args.headless:
            # Display the HUD window
            cv2.imshow("AAMAS - High-Contrast Neon HUD Monitor", frame)

        # ----------------- PRIVACY-BY-DESIGN FRAME ERASURE -----------------
        # Explicitly delete frame variables to purge raw image pixels from RAM on every iteration
        del frame
        del overlay
        if classification and (
            classification["is_stimming"] or classification.get("is_avoidance", False)
        ):
            del banner_overlay

    # Clean up and release webcam and MediaPipe resources
    stream.release()
    gateway.shutdown()
    extractor.release()
    cv2.destroyAllWindows()
    logger.info("Webcam released and system shutdown gracefully.")


if __name__ == "__main__":
    main()

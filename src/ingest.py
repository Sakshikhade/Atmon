"""Recorded-video ingest CLI.

Runs the detection pipeline headlessly over pre-recorded video files (or directories
of videos), extracting skeletal features, detecting behavior episodes, capturing
privacy-preserving landmark coordinate snapshots for skeleton replay, saving
episodes to local SQLite storage (data/outbox.db), logging frame features to CSV,
and dispatching real-time alerts.

Usage:
    python -m src.ingest path/to/video.mp4
    python -m src.ingest path/to/folder/   # batch
"""

import argparse
import datetime
import os
from collections import deque

import cv2

from src.alert_gateway import AlertGateway
from src.data_logger import DataLogger
from src.env_config import load_environment, print_env_banner
from src.event_storage import EventStorage, build_snapshot
from src.logging_config import configure_logging, get_logger
from src.pipeline import EventTracker, build_detectors, compute_normalized_distance

logger = get_logger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".m4v"}


def _iso(epoch: float | None) -> str:
    """Format an epoch timestamp as an ISO-8601 string."""
    ts = epoch if epoch is not None else datetime.datetime.now(datetime.UTC).timestamp()
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).isoformat()


def ingest_video(
    path: str,
    extractor,
    classifier,
    ears_closed_detector,
    storage: EventStorage | None = None,
    gateway: AlertGateway | None = None,
    data_logger: DataLogger | None = None,
) -> int:
    """Process a single video file, logging data locally, saving episodes to SQLite, and dispatching alerts.

    Returns the number of resolved behavior events detected.
    """
    cap = cv2.VideoCapture(path)
    if not cap.isOpened():
        logger.error("Could not open video: %s", path)
        return 0

    raw_fps = cap.get(cv2.CAP_PROP_FPS)
    try:
        fps = float(raw_fps)
        if fps <= 0 or fps > 120:
            fps = 30.0
    except (TypeError, ValueError):
        fps = 30.0

    tracker = EventTracker()
    recorded = 0
    frame_index = 0
    landmark_buffer = deque(maxlen=60)
    video_start_time = datetime.datetime.now(datetime.UTC).timestamp()

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                break
            frame_index += 1
            now = video_start_time + (frame_index / fps)

            landmarks_data = extractor.extract_landmarks(frame)
            ears_closed_signal = ears_closed_detector.update(landmarks_data)
            pose_lm = landmarks_data.get("pose_landmarks")

            if pose_lm:
                landmark_buffer.append((now, pose_lm))
                normalized_dist = compute_normalized_distance(pose_lm)
                classification = classifier.update(
                    normalized_dist,
                    ear_coverage_dist=landmarks_data.get("ear_coverage_dist", 999.0),
                    eye_openness_ratio=landmarks_data.get("eye_openness_ratio", 1.0),
                )
            else:
                classification = None
                normalized_dist = 0.0

            ears_closed_stimming = bool(ears_closed_signal.get("both_ears_closed_stimming"))
            stimming = bool(classification and classification.get("is_stimming"))
            ears_covered = bool(
                classification and classification.get("ears_covered") and not ears_closed_stimming
            )
            eyes_closed = bool(
                classification and classification.get("eyes_closed") and not ears_closed_stimming
            )

            # Log frame data locally if data_logger is provided
            if data_logger and pose_lm and classification:
                if stimming or ears_closed_stimming:
                    lbl = 1
                elif ears_covered or eyes_closed:
                    lbl = 2
                else:
                    lbl = 0

                data_logger.log_frame(
                    frame_index=frame_index,
                    mean_dist=classification.get("normalized_dist", normalized_dist),
                    std_dist=classification.get("std_dist", 0.0),
                    mean_vel=classification.get("mean_velocity", 0.0),
                    peak_freq=classification.get("frequency", 0.0),
                    mean_ear_coverage=landmarks_data.get("ear_coverage_dist", 999.0),
                    mean_eye_openness=landmarks_data.get("eye_openness_ratio", 1.0),
                    label=lbl,
                )

            _freq = classification.get("frequency", 0.0) if classification else 0.0
            _vel = classification.get("mean_velocity", 0.0) if classification else 0.0

            for ev in tracker.update(
                stimming=stimming or ears_closed_stimming,
                ears_covered=ears_covered,
                eyes_closed=eyes_closed,
                now=now,
            ):
                if gateway:
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
                        else:
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
                    else:
                        detail = ev.behavior
                        if ev.phase == "start":
                            gateway.send_alert(
                                event_status="avoidance_start",
                                frequency=_freq,
                                mean_velocity=_vel,
                                duration=0.0,
                                event_type="avoidance_start",
                                detail=detail,
                            )
                        else:
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
                    if storage:
                        storage.add_event(
                            type=ev.event_type,
                            subtype=ev.subtype,
                            action=ev.action,
                            started_at=_iso(ev.started_at),
                            ended_at=_iso(ev.ended_at),
                            duration_s=round(ev.duration_s, 2),
                            intensity=round(_vel, 3) if _vel > 0 else None,
                            source="ingest",
                            snapshot=snapshot,
                        )
                    recorded += 1
                    logger.info(
                        "Resolved event [%s/%s] duration=%.2fs (snapshot=%s)",
                        ev.event_type,
                        ev.subtype,
                        ev.duration_s,
                        "yes" if snapshot else "no",
                    )

    finally:
        cap.release()

    return recorded


def _video_files(path: str) -> list[str]:
    """Return a sorted list of supported video file paths for a single file or directory."""
    if os.path.isdir(path):
        return [
            os.path.join(path, f)
            for f in sorted(os.listdir(path))
            if os.path.splitext(f)[1].lower() in VIDEO_EXTENSIONS
        ]
    elif os.path.isfile(path):
        return [path]
    return []


def ingest_path(
    path: str,
    mode: str = "heuristic",
    model_path: str = "models/stimming_classifier.pkl",
    db_path: str = "data/outbox.db",
    csv_path: str | None = None,
    send_alerts: bool = True,
) -> int:
    """Ingest a single video file or an entire directory in batch."""
    files = _video_files(path)
    if not files:
        if os.path.isdir(path):
            logger.warning("No supported video files found in directory: %s", path)
        else:
            logger.error("Path does not exist: %s", path)
        return 0

    logger.info("Initializing detection pipeline in %s mode...", mode)
    extractor, classifier, ears_closed_detector = build_detectors(mode=mode, model_path=model_path)
    storage = EventStorage(db_path=db_path)
    gateway = AlertGateway() if send_alerts else None
    data_logger = DataLogger(output_path=csv_path) if csv_path else None

    total_events = 0
    try:
        for idx, file_path in enumerate(files, 1):
            logger.info("Processing [%d/%d]: %s", idx, len(files), file_path)
            events_count = ingest_video(
                path=file_path,
                extractor=extractor,
                classifier=classifier,
                ears_closed_detector=ears_closed_detector,
                storage=storage,
                gateway=gateway,
                data_logger=data_logger,
            )
            logger.info("Finished %s: detected %d event(s)", file_path, events_count)
            total_events += events_count
    finally:
        extractor.release()

    logger.info("Ingestion complete. Total events recorded: %d", total_events)
    return total_events


def main():
    parser = argparse.ArgumentParser(
        description="AAMAS Recorded Video Ingest CLI — detect behaviors, buffer skeleton snapshots, and persist events to SQLite."
    )
    parser.add_argument(
        "video_path",
        type=str,
        help="Path to a video file (e.g. samples/sample-2.mp4) or directory of videos",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["heuristic", "ml"],
        default="heuristic",
        help="Detection mode: 'heuristic' (rule-based) or 'ml' (Random Forest)",
    )
    parser.add_argument(
        "--model-path",
        type=str,
        default="models/stimming_classifier.pkl",
        help="Path to trained classifier model (for ml mode)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default="data/outbox.db",
        help="Path to local SQLite database (default: data/outbox.db)",
    )
    parser.add_argument(
        "--csv-path",
        type=str,
        default=None,
        help="Optional destination path for logging frame metrics to CSV",
    )
    parser.add_argument(
        "--no-alert",
        action="store_true",
        help="Disable webhook/SMS alerting during ingestion",
    )
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )
    args = parser.parse_args()
    configure_logging(args.log_level)

    env = load_environment()
    print_env_banner(env)

    ingest_path(
        path=args.video_path,
        mode=args.mode,
        model_path=args.model_path,
        db_path=args.db_path,
        csv_path=args.csv_path,
        send_alerts=not args.no_alert,
    )


if __name__ == "__main__":
    main()

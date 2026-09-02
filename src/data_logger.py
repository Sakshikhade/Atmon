import csv
import datetime
import os

from src.logging_config import get_logger

logger = get_logger(__name__)


class DataLogger:
    """
    DataLogger records real-time skeletal features and behavior labels (normal vs stimming)
    into a structured CSV file for machine learning baseline model training.
    """

    def __init__(self, filename="data/behavior_log.csv"):
        self.filename = filename
        self._initialize_csv()

    def _initialize_csv(self):
        # Create directory structure if needed
        os.makedirs(os.path.dirname(self.filename), exist_ok=True)

        # Write header if file does not exist or is empty
        if not os.path.exists(self.filename) or os.path.getsize(self.filename) == 0:
            with open(self.filename, mode="w", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(
                    [
                        "timestamp",
                        "frame_index",
                        "mean_distance",
                        "std_distance",
                        "mean_velocity",
                        "peak_frequency",
                        "mean_ear_coverage",
                        "mean_eye_openness",
                        "label",
                    ]
                )

    def log_frame(
        self,
        frame_index,
        mean_dist,
        std_dist,
        mean_vel,
        peak_freq,
        mean_ear_coverage,
        mean_eye_openness,
        label,
    ):
        """
        Appends a single annotated frame feature row to the log file.
        """
        timestamp = datetime.datetime.utcnow().isoformat() + "Z"
        try:
            with open(self.filename, mode="a", newline="") as file:
                writer = csv.writer(file)
                writer.writerow(
                    [
                        timestamp,
                        frame_index,
                        round(mean_dist, 4),
                        round(std_dist, 4),
                        round(mean_vel, 4),
                        round(peak_freq, 2),
                        round(mean_ear_coverage, 4),
                        round(mean_eye_openness, 4),
                        int(label),
                    ]
                )
        except Exception as e:
            logger.error("Failed to write to %s: %s", self.filename, e)

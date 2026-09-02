"""Unit tests for the recorded-video ingest orchestration (src/ingest.py).

The MediaPipe extractor, classifier, and cv2.VideoCapture are mocked — no real
video/model needed. Verifies that behavior transitions trigger alerts via
AlertGateway, frames are logged to DataLogger, and directory batch iteration works.
"""

import os
from unittest.mock import MagicMock, patch

from src.ingest import _video_files, ingest_video


class _FakeClassifier:
    """Returns is_stimming=True for a middle window of frames, else False.

    Drives one stimming start→resolved cycle over the frame sequence.
    """

    def __init__(self, on_frames):
        self.on_frames = on_frames
        self.i = 0

    def update(self, normalized_dist, **kwargs):
        self.i += 1
        on = self.i in self.on_frames
        return {
            "is_stimming": on,
            "ears_covered": False,
            "eyes_closed": False,
            "frequency": 4.0,
            "mean_velocity": 0.05,
            "activation_ratio": 0.6,
            "normalized_dist": normalized_dist,
            "std_dist": 0.01,
        }


def _pose_with_coords():
    """33 landmarks with numeric coords on the joints compute_normalized_distance uses."""
    lm = [MagicMock() for _ in range(33)]
    coords = {11: (0.4, 0.5, 0.0), 12: (0.6, 0.5, 0.0), 15: (0.4, 0.7, 0.0), 16: (0.6, 0.5, 0.0)}
    for i, (x, y, z) in coords.items():
        lm[i].x, lm[i].y, lm[i].z = x, y, z
    return lm


def _extractor_returning_pose():
    ex = MagicMock()
    ex.extract_landmarks.return_value = {
        "pose_landmarks": _pose_with_coords(),
        "ear_coverage_dist": 999.0,
        "eye_openness_ratio": 1.0,
    }
    return ex


def _ears_detector_off():
    d = MagicMock()
    d.update.return_value = {"both_ears_closed_stimming": False}
    return d


def _mock_capture(num_frames):
    """A cv2.VideoCapture mock that yields num_frames then stops."""
    cap = MagicMock()
    cap.isOpened.return_value = True
    seq = [(True, MagicMock()) for _ in range(num_frames)] + [(False, None)]
    cap.read.side_effect = seq
    return cap


def test_ingest_video_dispatches_alerts_and_logs():
    classifier = _FakeClassifier(on_frames={3, 4, 5, 6})
    extractor = _extractor_returning_pose()
    ears = _ears_detector_off()
    gateway = MagicMock()
    data_logger = MagicMock()
    storage = MagicMock()

    with patch("src.ingest.cv2.VideoCapture", return_value=_mock_capture(10)):
        recorded = ingest_video(
            "fake.mp4",
            extractor,
            classifier,
            ears,
            storage=storage,
            gateway=gateway,
            data_logger=data_logger,
        )

    assert recorded == 1
    # Storage should receive the resolved event with snapshot
    assert storage.add_event.call_count == 1
    storage_args = storage.add_event.call_args.kwargs
    assert storage_args["type"] == "stimming"
    assert storage_args["source"] == "ingest"
    assert storage_args["snapshot"] is not None
    assert "frames" in storage_args["snapshot"]

    # Gateway should have received stimming_start and stimming_resolved
    assert gateway.send_alert.call_count == 2
    first_call_args = gateway.send_alert.call_args_list[0].kwargs
    assert first_call_args["event_status"] == "stimming_start"
    second_call_args = gateway.send_alert.call_args_list[1].kwargs
    assert second_call_args["event_status"] == "stimming_resolved"

    # DataLogger should have logged all 10 frames
    assert data_logger.log_frame.call_count == 10


def test_ingest_video_no_events_when_never_stimming():
    classifier = _FakeClassifier(on_frames=set())  # never on
    gateway = MagicMock()
    data_logger = MagicMock()

    with patch("src.ingest.cv2.VideoCapture", return_value=_mock_capture(8)):
        recorded = ingest_video(
            "fake.mp4",
            _extractor_returning_pose(),
            classifier,
            _ears_detector_off(),
            gateway=gateway,
            data_logger=data_logger,
        )

    assert recorded == 0
    assert gateway.send_alert.call_count == 0
    assert data_logger.log_frame.call_count == 8


def test_ingest_video_unopenable_returns_zero():
    cap = MagicMock()
    cap.isOpened.return_value = False
    with patch("src.ingest.cv2.VideoCapture", return_value=cap):
        recorded = ingest_video(
            "missing.mp4",
            _extractor_returning_pose(),
            _FakeClassifier(set()),
            _ears_detector_off(),
        )
    assert recorded == 0


def test_video_files_single_file(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_text("")
    assert list(_video_files(str(f))) == [str(f)]


def test_video_files_directory_batch(tmp_path):
    # Two videos + one non-video; expect only the videos, sorted.
    (tmp_path / "b.mov").write_text("")
    (tmp_path / "a.mp4").write_text("")
    (tmp_path / "notes.txt").write_text("")
    found = [os.path.basename(p) for p in _video_files(str(tmp_path))]
    assert found == ["a.mp4", "b.mov"]

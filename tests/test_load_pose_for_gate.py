"""load_pose_for must extract keypoints when only the wrist gate needs them."""

from src.config import load_config
from src.pipeline import load_pose_for


GATE_ONLY_CONFIG = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
tau_high: 0.5
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
pose:
  enabled: false
  model_path: models/pose_landmarker.task
crop:
  enabled: false
hands:
  enabled: false
classes:
  ear_cover:
    require_wrist_near_ear: true
    wrist_near_ear_threshold: 0.28
  hair_twirling:
    allow_flip: true
"""

NO_GATE_CONFIG = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
tau_high: 0.5
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
pose:
  enabled: false
crop:
  enabled: false
classes:
  hair_twirling:
    allow_flip: true
"""


def test_load_pose_for_skips_when_gate_and_stream_off(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(NO_GATE_CONFIG, encoding="utf-8")
    cfg = load_config(str(path))
    bundle, templates = load_pose_for(cfg, "unused.mp4", "vid")
    assert bundle is None and templates is None


def test_load_pose_for_encodes_when_only_wrist_gate(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    path.write_text(GATE_ONLY_CONFIG, encoding="utf-8")
    cfg = load_config(str(path))

    fake_bundle = {"sequences": object(), "boxes": None}

    def fake_encode(cfg, video_path, video_id, force=False):
        return fake_bundle

    monkeypatch.setattr("src.pose.encode_video_pose", fake_encode)
    monkeypatch.setattr("src.pose.load_pose_templates", lambda cfg: {"should_not": "load"})

    bundle, templates = load_pose_for(cfg, "clip.mp4", "clip_id")
    assert bundle is fake_bundle
    assert templates is None, "DTW templates must stay off when pose.enabled is false"


def test_load_pose_for_fails_soft_on_gate_only_encode_error(tmp_path, monkeypatch, capsys):
    path = tmp_path / "config.yaml"
    path.write_text(GATE_ONLY_CONFIG, encoding="utf-8")
    cfg = load_config(str(path))

    def boom(*_a, **_k):
        raise FileNotFoundError("pose_landmarker.task missing")

    monkeypatch.setattr("src.pose.encode_video_pose", boom)

    bundle, templates = load_pose_for(cfg, "clip.mp4", "clip_id")
    assert bundle is None and templates is None
    out = capsys.readouterr().out
    assert "wrist gate" in out.lower() or "continuing without" in out.lower()

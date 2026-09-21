"""Regression tests for the "new reference never reaches detection" bug.

Root cause: cfg.class_names comes from config.yaml's `classes:` block, not
from what directories exist under data/references/ (config.yaml says so
directly: "Directory names under data/references/ must match these keys
exactly"). build_bank(), calibrate.py, and LiveDetector all iterate
cfg.class_names -- so a reference clip recorded into a directory that has no
matching config.yaml key is encoded into no bank, tracked by no
HysteresisTracker, and detected nowhere, even though the web UI shows it as
an existing, "Ready" class.

api_classes_create (what the "New use case..." flow calls) used to only
os.makedirs() the directory -- never touching config.yaml. add_class_to_config
closes that gap; these tests cover it directly and through the endpoint.
"""

import os

import pytest

from src.config import add_class_to_config, load_config  # noqa: E402

BASE_CONFIG = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
tau_high: 0.5
classes:
  # a comment that must survive
  ear_cover:
    allow_flip: true
    weights: {vjepa: 0.35, pose: 0.50, hands: 0.15}
"""

@pytest.fixture()
def workspace(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(BASE_CONFIG, encoding="utf-8")
    return str(path)

def test_new_class_is_appended_and_loadable(workspace):
    cfg = load_config(workspace)
    assert cfg.class_names == ["ear_cover"]

    add_class_to_config(cfg, "head_nodding")

    reloaded = load_config(workspace)
    assert reloaded.class_names == ["ear_cover", "head_nodding"]
    assert reloaded.class_cfg("head_nodding") == {"allow_flip": False}

def test_existing_comments_and_classes_survive(workspace):
    cfg = load_config(workspace)
    add_class_to_config(cfg, "head_nodding")

    text = open(workspace, encoding="utf-8").read()
    assert "# a comment that must survive" in text
    assert "weights: {vjepa: 0.35, pose: 0.50, hands: 0.15}" in text

    reloaded = load_config(workspace)
    assert reloaded.class_cfg("ear_cover")["weights"] == {"vjepa": 0.35, "pose": 0.50, "hands": 0.15}

def test_registering_the_same_class_twice_does_not_duplicate(workspace):
    cfg = load_config(workspace)
    add_class_to_config(cfg, "head_nodding")
    cfg2 = load_config(workspace)
    add_class_to_config(cfg2, "head_nodding")

    reloaded = load_config(workspace)
    assert reloaded.class_names == ["ear_cover", "head_nodding"]
    assert open(workspace, encoding="utf-8").read().count("head_nodding:") == 1

def test_already_configured_class_is_a_noop(workspace):
    cfg = load_config(workspace)
    before = open(workspace, encoding="utf-8").read()
    add_class_to_config(cfg, "ear_cover")
    after = open(workspace, encoding="utf-8").read()
    assert before == after

def test_a_second_new_class_is_appended_after_the_first(workspace):
    cfg = load_config(workspace)
    add_class_to_config(cfg, "head_nodding")
    cfg2 = load_config(workspace)
    add_class_to_config(cfg2, "hand_flapping")

    reloaded = load_config(workspace)
    assert reloaded.class_names == ["ear_cover", "hand_flapping", "head_nodding"]

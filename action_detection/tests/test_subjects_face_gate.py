"""Subjects store, face-match math, and Active-Subject offline gate."""

import os
import tempfile

import numpy as np
import pytest

from src.config import load_config
from src.face_id import (
    cosine_match,
    l2_normalize,
    save_gallery,
)
from src.scoring import group_detections
from src.subjects import (
    bind_subject,
    create_subject,
    has_face_gallery,
    list_subjects,
    migrate_legacy_references,
    subject_references_dir,
)


MIN_CFG = """
model_id: stub-encoder-v1
working_fps: 8
chunk_sec: 1.0
window_scales: [1.0]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 1
tau_high: 0.5
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
frames_per_clip: 8
identity:
  enabled: true
  match_threshold: 0.5
classes:
  ear_cover:
    allow_flip: false
"""


@pytest.fixture()
def workspace():
    with tempfile.TemporaryDirectory() as d:
        with open(os.path.join(d, "config.yaml"), "w", encoding="utf-8") as fh:
            fh.write(MIN_CFG)
        yield d


@pytest.fixture()
def cfg(workspace):
    return load_config(os.path.join(workspace, "config.yaml"), w_base_sec=1.0)


def test_cosine_match_above_and_below_threshold():
    a = l2_normalize(np.ones(512, dtype=np.float32))
    gallery = np.stack([a, l2_normalize(np.random.default_rng(0).normal(size=512))])
    assert cosine_match(a, gallery, threshold=0.99) is True
    far = l2_normalize(np.random.default_rng(1).normal(size=512))
    assert cosine_match(far, gallery, threshold=0.99) is False


def test_create_and_list_subjects(cfg):
    meta = create_subject(cfg, "Alex")
    assert meta["id"]
    assert list_subjects(cfg)[0]["display_name"] == "Alex"
    bind_subject(cfg, meta["id"])
    assert cfg["_subject_id"] == meta["id"]
    assert os.path.isdir(subject_references_dir(cfg, meta["id"]))


def test_migrate_legacy_references(cfg, workspace):
    legacy = os.path.join(workspace, "data", "references", "ear_cover")
    os.makedirs(legacy)
    # Tiny placeholder "clip" so migration sees content
    open(os.path.join(legacy, "ref_test.mp4"), "wb").write(b"not-a-real-mp4")
    sid = migrate_legacy_references(cfg, verbose=False)
    assert sid == "default"
    dest = subject_references_dir(cfg, "default")
    assert os.path.isfile(os.path.join(dest, "ear_cover", "ref_test.mp4"))
    # Second call is a no-op
    assert migrate_legacy_references(cfg, verbose=False) is None


def test_save_gallery_and_has_face(cfg):
    meta = create_subject(cfg, "Sam", subject_id="sam")
    emb = l2_normalize(np.random.default_rng(2).normal(size=(3, 512)))
    save_gallery(cfg, meta["id"], emb)
    assert has_face_gallery(cfg, meta["id"]) is True


class GateCfg(dict):
    def __init__(self, **kw):
        classes = kw.pop("classes", {"ear_cover": {}})
        super().__init__(
            chunk_sec=1.0,
            w_base_sec=2.0,
            w_base_chunks=2,
            window_scales=[1.0],
            stride_ratio=0.25,
            topk_prototypes=3,
            smoothing_windows=1,
            tau_low_ratio=0.85,
            min_duration_ratio=0.5,
            nms_tiou=0.5,
            identity={"enabled": True, "match_threshold": 0.4},
            classes=classes,
            **kw,
        )
        self.class_names = list(classes.keys())

    def class_cfg(self, name):
        return self.get("classes", {}).get(name, {})


def test_offline_face_gate_blocks_and_allows():
    cfg = GateCfg()
    starts = np.arange(20, dtype=np.float32)
    grid = np.full((1, 20), 0.2, dtype=np.float32)
    grid[0, 5:12] = 0.95
    blocked = np.zeros(20, dtype=bool)
    assert group_detections(
        ["ear_cover"], grid, starts, cfg, tau_high=0.8,
        face_match_per_chunk=blocked,
    ) == []
    allowed = np.zeros(20, dtype=bool)
    allowed[5:12] = True
    dets = group_detections(
        ["ear_cover"], grid, starts, cfg, tau_high=0.8,
        face_match_per_chunk=allowed,
    )
    assert len(dets) == 1


def test_bank_path_is_subject_scoped(cfg):
    from src.prototypes import bank_path

    meta = create_subject(cfg, "Pat", subject_id="pat")
    bind_subject(cfg, meta["id"])
    path = bank_path(cfg)
    assert "/prototypes/pat/" in path.replace("\\", "/")

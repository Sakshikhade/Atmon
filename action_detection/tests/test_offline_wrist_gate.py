"""Offline group_detections must honor the ear_cover wrist-near-ear gate.

Live already blocks opens via allow_open; offline previously ignored pose and
could open chin-rest FPs that live would refuse. These tests lock that parity.
"""

import numpy as np

from src.pose import L_SHOULDER, R_SHOULDER, UPPER_BODY
from src.scoring import group_detections

K = len(UPPER_BODY)


class GateCfg(dict):
    def __init__(self, **kw):
        classes = kw.pop("classes", {
            "ear_cover": {
                "require_wrist_near_ear": True,
                "wrist_near_ear_threshold": 0.28,
            },
        })
        super().__init__(
            chunk_sec=1.0,
            w_base_sec=2.0,
            w_base_chunks=2,
            window_scales=[0.7, 1.0, 1.4],
            stride_ratio=0.25,
            topk_prototypes=3,
            smoothing_windows=1,
            tau_low_ratio=0.85,
            min_duration_ratio=0.5,
            nms_tiou=0.5,
            classes=classes,
            **kw,
        )
        self.class_names = list(classes.keys())

    def class_cfg(self, name):
        return self.get("classes", {}).get(name, {})


def _normalized_chunk(wrist_near=True, frames=4):
    """One chunk of already-normalized pose (shoulders at +/-0.5)."""
    seq = np.full((frames, K, 3), np.nan, dtype=np.float32)
    for t in range(frames):
        seq[t, L_SHOULDER] = (-0.5, 0.0, 0.0)
        seq[t, R_SHOULDER] = (0.5, 0.0, 0.0)
        seq[t, 7] = (-0.4, -0.6, 0.0)   # L_EAR
        seq[t, 8] = (0.4, -0.6, 0.0)
        if wrist_near:
            seq[t, 15] = (-0.35, -0.55, 0.0)  # L_WRIST near ear
            seq[t, 16] = (0.5, 0.8, 0.0)
        else:
            seq[t, 15] = (0.5, 0.8, 0.0)
            seq[t, 16] = (0.5, 0.8, 0.0)
    return seq


def _high_score_grid(n=20, start=5, end=12):
    starts = np.arange(n, dtype=np.float32)
    grid = np.full((1, n), 0.2, dtype=np.float32)
    grid[0, start:end] = 0.95
    return grid, starts


def test_offline_gate_opens_when_wrist_near_ear():
    cfg = GateCfg()
    grid, starts = _high_score_grid()
    seqs = np.stack([_normalized_chunk(wrist_near=True) for _ in range(len(starts))])
    dets = group_detections(
        ["ear_cover"], grid, starts, cfg, tau_high=0.8, pose_sequences=seqs)
    assert len(dets) == 1
    assert dets[0]["class"] == "ear_cover"


def test_offline_gate_refuses_high_scores_without_wrist():
    cfg = GateCfg()
    grid, starts = _high_score_grid()
    seqs = np.stack([_normalized_chunk(wrist_near=False) for _ in range(len(starts))])
    dets = group_detections(
        ["ear_cover"], grid, starts, cfg, tau_high=0.8, pose_sequences=seqs)
    assert dets == [], "chin-rest-like pose must not open ear_cover offline"


def test_offline_gate_missing_sequences_still_opens_unguarded():
    """Paranoia: no sequences + gated class → warn and allow (appearance-only)."""
    cfg = GateCfg()
    grid, starts = _high_score_grid()
    dets = group_detections(
        ["ear_cover"], grid, starts, cfg, tau_high=0.8, pose_sequences=None)
    assert len(dets) == 1


def test_ungated_class_ignores_pose_sequences():
    cfg = GateCfg(classes={"hair_twirling": {}})
    grid, starts = _high_score_grid()
    seqs = np.stack([_normalized_chunk(wrist_near=False) for _ in range(len(starts))])
    dets = group_detections(
        ["hair_twirling"], grid, starts, cfg, tau_high=0.8, pose_sequences=seqs)
    assert len(dets) == 1

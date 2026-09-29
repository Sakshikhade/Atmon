#!/usr/bin/env python3
"""A/B the streams and the crop against a labelled eval set.

Answers one question: does each addition actually earn its place? Runs every
configuration over the same labelled video and reports the per-class margin in
sigma, so the comparison is like-for-like.

Diagnostic only. It reports chunk-level separability, which is the right
instrument for "can the representation see this behaviour" -- and the WRONG one
for reporting system performance (spec 8, 14.4). Event-level precision/recall
and false alarms per hour remain calibrate.py's job.
"""

import argparse
import copy
import itertools
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.evaluate import load_label_dir  # noqa: E402
from src.pipeline import grid_for_video  # noqa: E402
from src.prototypes import load_bank  # noqa: E402
from src.scoring import smooth  # noqa: E402


def find_video(video_id, videos_dir):
    for ext in (".mp4", ".mov", ".avi", ".mkv", ".MOV", ".MP4"):
        p = os.path.join(videos_dir, video_id + ext)
        if os.path.exists(p):
            return p
    raise SystemExit("no video for %r in %s" % (video_id, videos_dir))


def split_scores(series, starts, chunk_sec, spans):
    action, background = [], []
    for t, score in enumerate(series):
        if t >= len(starts):
            break
        mid = float(starts[t]) + chunk_sec / 2.0
        (action if any(a <= mid <= b for a, b in spans) else background).append(float(score))
    return np.asarray(action), np.asarray(background)


def dprime(action, background):
    """Signal-detection d'. Scale-free and standard, unlike a raw percentile gap."""
    if len(action) < 2 or len(background) < 2:
        return float("nan")
    pooled = np.sqrt(0.5 * (action.var(ddof=1) + background.var(ddof=1)))
    if pooled < 1e-9:
        return float("nan")
    return float((action.mean() - background.mean()) / pooled)


def auroc(action, background):
    """Rank-based AUC: P(a random action chunk outscores a random background one)."""
    if len(action) == 0 or len(background) == 0:
        return float("nan")
    values = np.concatenate([action, background])
    order = values.argsort()
    ranks = np.empty(len(values), dtype=np.float64)
    ranks[order] = np.arange(1, len(values) + 1)
    # average ranks for ties
    _, inv, counts = np.unique(values, return_inverse=True, return_counts=True)
    sums = np.zeros(len(counts))
    np.add.at(sums, inv, ranks)
    ranks = (sums / counts)[inv]
    r_action = ranks[: len(action)].sum()
    n_a, n_b = len(action), len(background)
    return float((r_action - n_a * (n_a + 1) / 2.0) / (n_a * n_b))


def evaluate_config(cfg, bank_cfg, labels_dir, videos_dir, quiet=True):
    bank, w_base = load_bank(bank_cfg)
    per_class = {}
    all_labels = load_label_dir(labels_dir)
    for labels in all_labels:
        video_path = find_video(labels["video_id"], videos_dir)
        _, bundle, class_names, grid, parts = grid_for_video(
            cfg, bank, video_path, verbose=not quiet
        )
        for ci, name in enumerate(class_names):
            spans = [(e["start"], e["end"]) for e in labels["events"] if e["class"] == name]
            series = smooth(grid[ci], cfg["smoothing_windows"])
            a, b = split_scores(series, bundle["starts"], bundle["chunk_sec"], spans)
            acc = per_class.setdefault(name, [[], []])
            acc[0].extend(a.tolist())
            acc[1].extend(b.tolist())
    out = {}
    for name, (a, b) in per_class.items():
        a, b = np.asarray(a), np.asarray(b)
        out[name] = {
            "margin": float(np.percentile(a, 50) - np.percentile(b, 90)) if len(a) and len(b) else float("nan"),
            "dprime": dprime(a, b),
            "auroc": auroc(a, b),
            "n_action": len(a),
        }
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--labels", default=None)
    parser.add_argument("--videos", default=None)
    args = parser.parse_args()

    base = load_config(args.config)
    labels_dir = args.labels or base.path("data", "labels")
    videos_dir = args.videos or base.path("data", "videos")

    # Each setting toggled independently against the same bank and labels.
    setups = []
    for crop_on, pose_on, hands_on in itertools.product([False, True], repeat=3):
        name = "+".join([s for s, on in
                         (("crop", crop_on), ("pose", pose_on), ("hands", hands_on)) if on]) or "vjepa only"
        setups.append((name, crop_on, pose_on, hands_on))

    rows = []
    for name, crop_on, pose_on, hands_on in setups:
        bank_cfg = load_config(args.config)
        bank_cfg["crop"]["enabled"] = crop_on
        _, w_base = load_bank(bank_cfg)
        cfg = load_config(args.config, w_base_sec=w_base)
        cfg["crop"]["enabled"] = crop_on
        cfg["pose"]["enabled"] = pose_on
        cfg["hands"]["enabled"] = hands_on
        print("\n=== %s ===" % name)
        try:
            result = evaluate_config(cfg, bank_cfg, labels_dir, videos_dir, quiet=False)
        except Exception as exc:  # noqa: BLE001
            print("  FAILED: %s" % exc)
            continue
        rows.append((name, result))
        for cls in sorted(result):
            r = result[cls]
            print("  %-16s margin %+.3f  d' %+.2f  AUROC %.3f  (n_action=%d)"
                  % (cls, r["margin"], r["dprime"], r["auroc"], r["n_action"]))

    print("\n%s" % ("=" * 78))
    classes = sorted({c for _, r in rows for c in r})
    header = "%-26s" % "setup" + "".join("%22s" % c for c in classes)
    print(header)
    print("-" * len(header))
    for name, result in rows:
        line = "%-26s" % name
        for c in classes:
            r = result.get(c)
            line += "%22s" % ("d' %+.2f AUC %.2f" % (r["dprime"], r["auroc"]) if r else "-")
        print(line)
    print("\nd' is signal-detection separability; AUROC is P(action > background).")
    print("AUROC 0.5 = no signal. These are DIAGNOSTIC; report event-level metrics.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

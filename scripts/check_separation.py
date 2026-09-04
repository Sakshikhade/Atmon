#!/usr/bin/env python3
"""Score distribution of labelled action vs background (spec pitfall 14.8).

Run this BEFORE calibrating. If background scores sit close to action scores,
the encoder is not discriminating and no threshold will save it -- that is the
signal to move to Phase 5, and it is much cheaper to learn here than after a
full sweep.

Also checks something the sweep cannot: whether `tau_low_ratio` produces a
release threshold that actually sits above the background level. Cosine
similarities from a single scene cluster near 1.0, and a multiplicative ratio
applied to a threshold of ~0.96 lands near ~0.82 -- below background, so a
detection would open and never close.
"""

import argparse
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.evaluate import load_label_dir  # noqa: E402
from src.pipeline import describe_fusion, grid_for_video, pose_available  # noqa: E402
from src.prototypes import load_bank  # noqa: E402
from src.scoring import smooth  # noqa: E402


def percentiles(values):
    if not len(values):
        return None
    return {p: float(np.percentile(values, p)) for p in (1, 10, 50, 90, 99)}


def fmt(stats):
    if stats is None:
        return "  (none)"
    return "  ".join("p%-2d %.4f" % (p, v) for p, v in sorted(stats.items()))


def find_video(video_id, videos_dir):
    for ext in (".mp4", ".mov", ".avi", ".mkv", ".MOV", ".MP4"):
        candidate = os.path.join(videos_dir, video_id + ext)
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("no video found for label %r in %s" % (video_id, videos_dir))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--labels", default=None)
    parser.add_argument("--videos", default=None)
    args = parser.parse_args()

    bank_cfg = load_config(args.config)
    bank, w_base_sec = load_bank(bank_cfg)
    cfg = load_config(args.config, w_base_sec=w_base_sec)

    labels_dir = args.labels or cfg.path("data", "labels")
    videos_dir = args.videos or cfg.path("data", "videos")
    all_labels = load_label_dir(labels_dir)

    streams = ["fused", "vjepa"] + (["pose"] if pose_available(cfg) else [])
    action = {s: {n: [] for n in sorted(bank)} for s in streams}
    background = {s: {n: [] for n in sorted(bank)} for s in streams}

    print("fusion weights:")
    print(describe_fusion(cfg, sorted(bank)))
    print()

    for labels in all_labels:
        video_path = find_video(labels["video_id"], videos_dir)
        _, bundle, class_names, grid, parts = grid_for_video(cfg, bank, video_path)
        starts, chunk_sec = bundle["starts"], bundle["chunk_sec"]
        # Standardize the individual streams too. Raw V-JEPA cosines and raw
        # pose similarities live on completely different scales, so comparing
        # their margins to each other -- or to the fused number -- is
        # meaningless until all three are in the same units.
        from src.normalize import zscore_grid

        grids = {"fused": grid, "vjepa": zscore_grid(parts["vjepa"])}
        if parts.get("pose") is not None:
            grids["pose"] = zscore_grid(parts["pose"])

        for stream, g in grids.items():
            if stream not in action:
                continue
            for ci, name in enumerate(class_names):
                series = smooth(g[ci], cfg["smoothing_windows"])
                spans = [(e["start"], e["end"]) for e in labels["events"] if e["class"] == name]
                for t, score in enumerate(series):
                    if t >= len(starts):
                        break
                    mid = float(starts[t]) + chunk_sec / 2.0
                    inside = any(a <= mid <= b for a, b in spans)
                    (action if inside else background)[stream][name].append(float(score))

    per_class_action = action["fused"]
    per_class_background = background["fused"]

    print()
    for stream in streams:
        parts_line = []
        for name in sorted(bank):
            A, B = np.asarray(action[stream][name]), np.asarray(background[stream][name])
            if len(A) and len(B):
                m = float(np.percentile(A, 50) - np.percentile(B, 90))
                parts_line.append("%s %+.3f" % (name, m))
        print("  margin by stream  %-6s  %s" % (stream, "   ".join(parts_line)))

    # With normalize_scores the grid is in sigma-above-background, so the
    # "thin margin" cutoff has to be in sigma too -- 0.02 would be meaningless
    # on a scale where 1.0 is one standard deviation of background.
    normalized = bool(cfg.get("normalize_scores", True))
    thin = 1.0 if normalized else 0.02
    units = "sigma" if normalized else "cosine"

    print("\n%s" % ("=" * 74))
    print("margins below are in %s (fused stream)" % units)
    worst_margin = None
    for name in sorted(bank):
        act = np.asarray(per_class_action[name])
        bg = np.asarray(per_class_background[name])
        print("\n%s   (%d action chunks, %d background chunks)" % (name, len(act), len(bg)))
        print("  action     %s" % fmt(percentiles(act)))
        print("  background %s" % fmt(percentiles(bg)))

        if len(act) and len(bg):
            # How far the typical action sits above the bulk of background.
            margin = float(np.percentile(act, 50) - np.percentile(bg, 90))
            print("  margin (action p50 - background p90): %+.4f %s" % (margin, units))
            worst_margin = margin if worst_margin is None else min(worst_margin, margin)
            if margin <= 0.0:
                print("    NO SEPARATION -- background outscores the action.")
            elif margin < thin:
                print("    VERY THIN -- thresholding will be brittle.")
            else:
                print("    usable")
        elif not len(act):
            print("  no labelled instances of this class; nothing to compare")

    # tau_low sanity: the release threshold must sit above background.
    everything = np.concatenate(
        [np.asarray(v) for v in list(per_class_action.values()) + list(per_class_background.values())
         if len(v)]
    )
    plausible_tau_high = float(np.percentile(everything, 95))
    tau_low = plausible_tau_high * float(cfg["tau_low_ratio"])
    background_all = np.concatenate(
        [np.asarray(v) for v in per_class_background.values() if len(v)]
    )
    background_p90 = float(np.percentile(background_all, 90)) if len(background_all) else 0.0

    print("\n%s" % ("=" * 74))
    print("hysteresis check")
    print("  a plausible tau_high (p95 of all scores) : %.4f" % plausible_tau_high)
    print("  tau_low = tau_high x %.2f                : %.4f" % (cfg["tau_low_ratio"], tau_low))
    print("  background p90                           : %.4f" % background_p90)
    if tau_low < background_p90:
        print(
            "\n  PROBLEM: tau_low sits BELOW background. A detection would open and\n"
            "  never close until the max_open_sec force-close, producing one huge\n"
            "  event per video."
        )
        if not normalized:
            print("  Set normalize_scores: true -- on the sigma scale zero already means\n"
                  "  'typical background', which is what the ratio assumes.")
    else:
        print("  OK: tau_low is above background, so detections can close.")

    if worst_margin is not None and worst_margin < thin:
        print(
            "\nVerdict: still too thin for prototype matching alone. If the pose\n"
            "stream did not help (compare the per-stream margins above), the next\n"
            "step is spec 11.2 -- a trained linear head on the cached features --\n"
            "which needs >= 20 labelled instances per weak class."
        )
    else:
        print("\nVerdict: usable separation. Calibrate next.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

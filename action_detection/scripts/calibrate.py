#!/usr/bin/env python3
"""Phase 4 -- sweep tau_high against a labeled eval set (spec 8).

Do not skip this and do not pick a threshold by eye. Event-level metrics and
false alarms per hour only; no frame-level accuracy, no AUC.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config, patch_config_tau_high, save_calibration  # noqa: E402
from src.evaluate import (  # noqa: E402
    confusion_matrix,
    evaluate,
    format_confusion,
    load_label_dir,
    prepare_video,
    select_threshold,
    sweep_thresholds,
    write_sweep_csv,
)
from src.pipeline import describe_fusion, grid_for_video  # noqa: E402
from src.prototypes import load_bank  # noqa: E402
from src.scoring import group_detections  # noqa: E402

MIN_INSTANCES = 20      # spec 8.1
MIN_MINUTES = 30


def find_video(cfg, video_id, videos_dir):
    for ext in (".mp4", ".mov", ".avi", ".mkv"):
        candidate = os.path.join(videos_dir, video_id + ext)
        if os.path.exists(candidate):
            return candidate
    raise SystemExit("no video found for label %r in %s" % (video_id, videos_dir))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--labels", default=None, help="directory of label JSON (default data/labels/)")
    parser.add_argument("--videos", default=None, help="directory of videos (default data/videos/)")
    parser.add_argument("--out", default=None, help="sweep CSV (default cache/threshold_sweep.csv)")
    parser.add_argument("--steps", type=int, default=60)
    parser.add_argument("--tiou", type=float, default=0.5)
    parser.add_argument(
        "--write-config",
        action="store_true",
        help="also patch the tau_high line in config.yaml (comments preserved)",
    )
    args = parser.parse_args()

    bank_cfg = load_config(args.config)
    bank, w_base_sec = load_bank(bank_cfg)
    cfg = load_config(args.config, w_base_sec=w_base_sec)

    labels_dir = args.labels or cfg.path("data", "labels")
    videos_dir = args.videos or cfg.path("data", "videos")
    all_labels = load_label_dir(labels_dir)

    n_instances = sum(len(lab["events"]) for lab in all_labels)
    total_minutes = sum(float(lab.get("duration") or 0.0) for lab in all_labels) / 60.0
    print("eval set  : %d videos, %d instances, %.1f minutes" % (
        len(all_labels), n_instances, total_minutes))
    if n_instances < MIN_INSTANCES or total_minutes < MIN_MINUTES:
        print(
            "  WARNING: spec 8.1 asks for >= %d instances across >= %d minutes.\n"
            "  A threshold calibrated on less than this is not trustworthy."
            % (MIN_INSTANCES, MIN_MINUTES)
        )

    print("\nfusion weights:")
    print(describe_fusion(cfg, cfg.class_names))

    prepared = []
    raw_by_class = {}
    for labels in all_labels:
        video_path = find_video(cfg, labels["video_id"], videos_dir)
        _, bundle, class_names, grid, parts = grid_for_video(cfg, bank, video_path)
        prepared.append(prepare_video(
            cfg, bank, bundle, labels, class_names, grid,
            pose_sequences=parts.get("_pose_sequences"),
        ))
        # Collect RAW appearance similarity so the live path can be given a
        # background scale. Live estimates spread from a short window and gets it
        # ~6x too small, which makes an offline-calibrated threshold meaningless
        # there; the scale belongs to the behaviour, not to the session.
        for ci, name in enumerate(class_names):
            raw_by_class.setdefault(name, {})[labels["video_id"]] = parts["vjepa"][ci]

    print("\nsweeping %d thresholds..." % args.steps)
    rows = sweep_thresholds(prepared, cfg, n_steps=args.steps, tiou_threshold=args.tiou)

    out_path = args.out or cfg.path("cache", "threshold_sweep.csv")
    write_sweep_csv(rows, out_path)
    print("sweep CSV : %s" % out_path)

    budget = float(cfg["max_false_alarms_per_hour"])
    best, within_budget = select_threshold(rows, budget)

    print("\n--- selected threshold ---")
    if not within_budget:
        print(
            "NO threshold met the budget of %.1f false alarms/hour.\n"
            "Reporting the lowest-false-alarm row instead. This is NOT a calibrated\n"
            "threshold -- the encoder is not separating these classes, which is the\n"
            "signal to move to Phase 5 (spec 11)." % budget
        )
    print("tau_high            : %.6f" % best["tau_high"])
    print("precision           : %.3f" % best["precision"])
    print("recall              : %.3f" % best["recall"])
    print("F1                  : %.3f" % best["f1"])
    print("false alarms / hour : %.2f  (budget %.1f)" % (best["false_alarms_per_hour"], budget))
    print("segment mAP @0.3    : %.3f" % best["map_0.3"])
    print("segment mAP @0.5    : %.3f" % best["map_0.5"])
    print("segment mAP @0.7    : %.3f" % best["map_0.7"])
    print("tp/fp/fn            : %d / %d / %d" % (best["tp"], best["fp"], best["fn"]))

    # Per-class confusion at the chosen threshold (spec 8.3).
    detections, ground_truth = [], []
    for p in prepared:
        detections.extend(
            group_detections(
                p["class_names"], p["grid"], p["starts"], cfg,
                best["tau_high"], p["chunk_sec"],
                pose_sequences=p.get("pose_sequences"),
            )
        )
        ground_truth.extend(p["events"])
    labels_axis, matrix = confusion_matrix(detections, ground_truth, cfg.class_names, args.tiou)
    print("\n--- confusion matrix at tau_high = %.4f ---" % best["tau_high"])
    print(format_confusion(labels_axis, matrix))

    total_hours = sum(p["duration"] for p in prepared) / 3600.0
    final = evaluate(detections, ground_truth, total_hours, args.tiou)
    if final["_missed"]:
        print("\nmissed events (%d):" % len(final["_missed"]))
        for gt in final["_missed"][:10]:
            print("  %-20s %8.2fs -> %8.2fs" % (gt["class"], gt["start"], gt["end"]))

    import numpy as np

    from src.normalize import robust_spread

    # Per video, then median across videos -- NEVER pooled. Raw similarity sits
    # at a different absolute level in each scene (measured: 0.81, 0.81, 0.52),
    # so pooling measures the gap between scenes, which is exactly the component
    # standardizing exists to remove. Pooled came out 2.8-3.0x the within-video
    # value and suppressed every live detection.
    background_scale = {}
    print("\nbackground scale of the raw appearance stream, per video:")
    for name in sorted(raw_by_class):
        per_video = {vid: float(robust_spread(row)) for vid, row in raw_by_class[name].items()}
        centres = {vid: float(np.median(row)) for vid, row in raw_by_class[name].items()}
        background_scale[name] = float(np.median(list(per_video.values())))
        print("  %-16s %s -> median %.4f"
              % (name, "  ".join("%s %.4f" % (v, per_video[v]) for v in sorted(per_video)),
                 background_scale[name]))
        spread_of_centres = max(centres.values()) - min(centres.values())
        if spread_of_centres > background_scale[name]:
            print("    NOTE: video centres span %.3f (%s), wider than the within-video"
                  % (spread_of_centres,
                     " ".join("%s %.2f" % (v, centres[v]) for v in sorted(centres))))
            print("    spread itself -- these scenes differ enough that a single scale is a"
                  "\n    compromise across them.")

    sidecar = save_calibration(
        cfg,
        best["tau_high"],
        {
            "background_scale": background_scale,
            "precision": best["precision"],
            "recall": best["recall"],
            "false_alarms_per_hour": best["false_alarms_per_hour"],
            "within_budget": within_budget,
            "tiou": args.tiou,
            "n_instances": n_instances,
        },
    )
    print("\ncalibration -> %s" % sidecar)
    if args.write_config:
        print("config.yaml -> %s (tau_high line patched)" % patch_config_tau_high(cfg, best["tau_high"]))
    else:
        print("(detect.py reads the sidecar automatically; --write-config also patches config.yaml)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

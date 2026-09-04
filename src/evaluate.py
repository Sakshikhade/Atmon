"""Event-level metrics and threshold calibration (spec 8).

Event level only. There is no frame-level accuracy or AUC anywhere in this file
and none should be added: on rare events those numbers look excellent while the
system produces tens of thousands of false alarms per hour (spec 8, 14.4).
"""

import json
import os

import numpy as np

from src.scoring import group_detections, score_grid, tiou

TIOU_LEVELS = (0.3, 0.5, 0.7)


def load_labels(path):
    """One ground-truth file (spec 4)."""
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    for key in ("video_id", "duration", "events"):
        if key not in data:
            raise ValueError("%s is missing required key %r" % (path, key))
    return data


def load_label_dir(directory):
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            out.append(load_labels(os.path.join(directory, name)))
    if not out:
        raise SystemExit("no label files in %s -- Phase 4 needs a hand-labeled eval set" % directory)
    return out


def match_detections(detections, ground_truth, tiou_threshold=0.5):
    """Greedy score-ordered matching. Each GT event matches at most one detection.

    Returns (matches, unmatched_detections, unmatched_gt) where matches are
    (detection, gt) pairs. A detection is a true positive only if it overlaps a
    SAME-CLASS ground-truth event with tIoU >= threshold (spec 8.2).
    """
    taken = set()
    matches = []
    false_positives = []

    for det in sorted(detections, key=lambda d: d["score"], reverse=True):
        best_i, best_iou = None, 0.0
        for i, gt in enumerate(ground_truth):
            if i in taken or gt["class"] != det["class"]:
                continue
            score = tiou((det["start"], det["end"]), (gt["start"], gt["end"]))
            if score >= tiou_threshold and score > best_iou:
                best_i, best_iou = i, score
        if best_i is None:
            false_positives.append(det)
        else:
            taken.add(best_i)
            matches.append((det, ground_truth[best_i]))

    missed = [gt for i, gt in enumerate(ground_truth) if i not in taken]
    return matches, false_positives, missed


def average_precision(detections, ground_truth, tiou_threshold):
    """AP over the given detection set, ranked by score (per class, then meaned)."""
    classes = sorted({gt["class"] for gt in ground_truth} | {d["class"] for d in detections})
    aps = []
    for name in classes:
        gts = [g for g in ground_truth if g["class"] == name]
        dets = sorted(
            [d for d in detections if d["class"] == name], key=lambda d: d["score"], reverse=True
        )
        if not gts:
            continue
        if not dets:
            aps.append(0.0)
            continue

        taken = set()
        tps = np.zeros(len(dets), dtype=np.float64)
        for j, det in enumerate(dets):
            best_i, best_iou = None, 0.0
            for i, gt in enumerate(gts):
                if i in taken:
                    continue
                score = tiou((det["start"], det["end"]), (gt["start"], gt["end"]))
                if score >= tiou_threshold and score > best_iou:
                    best_i, best_iou = i, score
            if best_i is not None:
                taken.add(best_i)
                tps[j] = 1.0

        cum_tp = np.cumsum(tps)
        cum_fp = np.cumsum(1.0 - tps)
        recall = cum_tp / len(gts)
        precision = cum_tp / np.maximum(cum_tp + cum_fp, 1e-12)

        # Interpolated AP: precision is made monotonically non-increasing.
        precision = np.maximum.accumulate(precision[::-1])[::-1]
        aps.append(float(np.sum(np.diff(np.concatenate([[0.0], recall])) * precision)))

    return float(np.mean(aps)) if aps else 0.0


def evaluate(detections, ground_truth, total_hours, tiou_threshold=0.5):
    """Event-level precision / recall / false alarms per hour, plus mAP."""
    matches, false_positives, missed = match_detections(detections, ground_truth, tiou_threshold)
    tp, fp, fn = len(matches), len(false_positives), len(missed)

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "false_alarms_per_hour": fp / total_hours if total_hours > 0 else float("inf"),
        "map": {t: average_precision(detections, ground_truth, t) for t in TIOU_LEVELS},
        "_matches": matches,
        "_false_positives": false_positives,
        "_missed": missed,
    }


def confusion_matrix(detections, ground_truth, class_names, tiou_threshold=0.5):
    """Counts of gt class -> predicted class, plus missed and spurious rows.

    Class confusion is what tells you two prototypes are too close together; a
    single precision number does not.
    """
    labels = list(class_names)
    index = {name: i for i, name in enumerate(labels)}
    n = len(labels)
    matrix = np.zeros((n + 1, n + 1), dtype=np.int64)  # extra row/col = background

    taken = set()
    for det in sorted(detections, key=lambda d: d["score"], reverse=True):
        best_i, best_iou = None, 0.0
        for i, gt in enumerate(ground_truth):
            if i in taken:
                continue
            score = tiou((det["start"], det["end"]), (gt["start"], gt["end"]))
            if score >= tiou_threshold and score > best_iou:
                best_i, best_iou = i, score
        if best_i is None:
            matrix[n, index.get(det["class"], n)] += 1          # background -> predicted
        else:
            taken.add(best_i)
            matrix[index.get(ground_truth[best_i]["class"], n), index.get(det["class"], n)] += 1

    for i, gt in enumerate(ground_truth):
        if i not in taken:
            matrix[index.get(gt["class"], n), n] += 1           # missed
    return labels + ["(background)"], matrix


def sweep_thresholds(prepared, cfg, n_steps=60, tiou_threshold=0.5):
    """Sweep tau_high across the observed score range (spec 8.2).

    `prepared` is a list of dicts from prepare_video(): each holds the score grid
    and ground truth for one video, so the encoder runs once and every threshold
    reuses the same features.
    """
    all_scores = np.concatenate([p["grid"].ravel() for p in prepared])
    lo = float(np.percentile(all_scores, 50))
    hi = float(all_scores.max())
    if hi <= lo:
        hi = lo + 1e-3
    taus = np.linspace(lo, hi, n_steps)

    total_hours = sum(p["duration"] for p in prepared) / 3600.0
    ground_truth = [ev for p in prepared for ev in p["events"]]

    rows = []
    for tau in taus:
        detections = []
        for p in prepared:
            detections.extend(
                group_detections(p["class_names"], p["grid"], p["starts"], cfg, tau, p["chunk_sec"])
            )
        result = evaluate(detections, ground_truth, total_hours, tiou_threshold)
        rows.append(
            {
                "tau_high": float(tau),
                "tp": result["tp"],
                "fp": result["fp"],
                "fn": result["fn"],
                "precision": result["precision"],
                "recall": result["recall"],
                "f1": result["f1"],
                "false_alarms_per_hour": result["false_alarms_per_hour"],
                "map_0.3": result["map"][0.3],
                "map_0.5": result["map"][0.5],
                "map_0.7": result["map"][0.7],
                "n_detections": len(detections),
            }
        )
    return rows


def select_threshold(rows, max_false_alarms_per_hour):
    """Highest-recall threshold inside the false-alarm budget (spec 8.4).

    Falls back to the lowest-FA row when nothing meets the budget, and says so --
    the caller must not silently present that as a calibrated threshold.
    """
    affordable = [r for r in rows if r["false_alarms_per_hour"] <= max_false_alarms_per_hour]
    if affordable:
        best = max(affordable, key=lambda r: (r["recall"], r["f1"]))
        return best, True
    return min(rows, key=lambda r: r["false_alarms_per_hour"]), False


def prepare_video(cfg, bank, feats_bundle, labels, class_names=None, grid=None):
    """Score grid + ground truth for one labeled video, computed once.

    Pass class_names/grid when the caller already fused the streams (pipeline
    .grid_for_video); otherwise the embedding stream is scored here alone.
    """
    if grid is None:
        class_names, grid = score_grid(feats_bundle["feats"], bank, cfg)
    duration = float(labels.get("duration") or 0.0)
    if duration <= 0:
        starts = feats_bundle["starts"]
        duration = float(starts[-1] + feats_bundle["chunk_sec"]) if len(starts) else 0.0
    return {
        "video_id": labels["video_id"],
        "class_names": class_names,
        "grid": grid,
        "starts": feats_bundle["starts"],
        "chunk_sec": feats_bundle["chunk_sec"],
        "duration": duration,
        "events": list(labels["events"]),
    }


def write_sweep_csv(rows, path):
    import csv

    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return path


def format_confusion(labels, matrix):
    width = max(len(name) for name in labels) + 2
    header = " " * width + "".join(name.rjust(width) for name in labels)
    lines = ["  (rows = ground truth, columns = predicted)", header]
    for i, name in enumerate(labels):
        lines.append(name.rjust(width) + "".join(str(v).rjust(width) for v in matrix[i]))
    return "\n".join(lines)

#!/usr/bin/env python3
"""Phase 3 -- detect actions in a video and record them (spec 7, 9).

Writes two things: dets.json for this invocation (what Phase 4 evaluates) and an
append to the durable event log (what survives across runs). Writing the log is
not optional and has no disable flag.
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chunker import video_id_for  # noqa: E402
from src.clip_writer import (  # noqa: E402
    build_store,
    clips_enabled,
    describe_store,
    extract_clips_from_video,
)
from src.config import load_config, require_tau_high  # noqa: E402
from src.event_log import (  # noqa: E402
    SOURCE_VIDEO,
    EventLogWriter,
    new_run_id,
    parse_iso,
    video_start_utc,
)
from src.pipeline import describe_fusion, grid_for_video  # noqa: E402
from src.prototypes import load_bank  # noqa: E402
from src.scoring import background_score_stats, group_detections  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--video", required=True)
    parser.add_argument("--out", default=None, help="detections JSON (default: <video_id>.dets.json)")
    parser.add_argument(
        "--source-start-utc",
        default=None,
        help="wall-clock origin of the recording, e.g. 2026-09-01T14:03:00Z (spec 9.3)",
    )
    parser.add_argument("--force", action="store_true", help="re-encode even if cached")
    parser.add_argument(
        "--no-clips", action="store_true", help="skip clip extraction even when clips.enabled"
    )
    args = parser.parse_args()

    bank_cfg = load_config(args.config)
    bank, w_base_sec = load_bank(bank_cfg)
    cfg = load_config(args.config, w_base_sec=w_base_sec)
    tau_high = require_tau_high(cfg)

    video_id = video_id_for(args.video)
    video_id, bundle, class_names, grid, parts = grid_for_video(
        cfg, bank, args.video, force=args.force
    )
    detections = group_detections(class_names, grid, bundle["starts"], cfg, tau_high, bundle["chunk_sec"])

    # Wall-clock origin: explicit flag, else opt-in mtime, else unknown (spec 9.3).
    start_utc = parse_iso(args.source_start_utc) if args.source_start_utc else None
    if start_utc is None:
        start_utc = video_start_utc(args.video, cfg["event_log"]["infer_video_start_from_mtime"])

    # Clips are cut from the source in one pass, before logging, so each row can
    # carry the path of its own clip.
    clip_paths = {}
    store = None
    if clips_enabled(cfg) and not args.no_clips and detections:
        store = build_store(cfg)
        clips_cfg = cfg["clips"]
        written = extract_clips_from_video(
            args.video,
            [{"event_id": d["event_id"], "start": d["start"], "end": d["end"]} for d in detections],
            store,
            source_id=video_id,
            pre_roll=float(clips_cfg.get("pre_roll_sec", 2.0)),
            post_roll=float(clips_cfg.get("post_roll_sec", 2.0)),
        )
        clip_paths = {eid: store.relative(p) for eid, p in written.items()}

    log_path = cfg.path(cfg["event_log"]["path"])
    with EventLogWriter(
        path=log_path,
        run_id=new_run_id(),
        source_id=video_id,
        source_type=SOURCE_VIDEO,
        model_id=cfg["model_id"],
        working_fps=cfg["working_fps"],
        chunk_sec=cfg["chunk_sec"],
        tau_high=tau_high,
        source_start_utc=start_utc,
        flush_each_event=bool(cfg["event_log"]["flush_each_event"]),
    ) as writer:
        for det in detections:
            # Offline grouping knows both boundaries, so every row is closed.
            writer.write_closed(
                det["class"], det["start"], det["end"], det["score"],
                event_id=det["event_id"], clip_path=clip_paths.get(det["event_id"]),
            )

    if store is not None:
        deleted, freed = store.enforce_budget()
        if deleted:
            print("clip retention: deleted %d old clip(s), freed %.2f GB"
                  % (deleted, freed / (1024 ** 3)))

    out_path = args.out or "%s.dets.json" % video_id
    payload = {
        "video_id": video_id,
        "duration": float(bundle["starts"][-1] + bundle["chunk_sec"]) if len(bundle["starts"]) else 0.0,
        "tau_high": tau_high,
        "events": [
            {"class": d["class"], "start": d["start"], "end": d["end"], "score": d["score"]}
            for d in detections
        ],
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    print("video     : %s (%d chunks)" % (video_id, len(bundle["starts"])))
    print("tau_high  : %.4f (from %s)" % (tau_high, cfg.get("_tau_high_source", "?")))
    print("W_base    : %.2fs -> %d chunks, windows %s" % (
        cfg["w_base_sec"], cfg.w_base_chunks, cfg.window_lengths_chunks()))
    print("detections: %d -> %s" % (len(detections), out_path))
    print("event log : %s" % log_path)
    if store is not None:
        print("clips     : %d written, %s" % (len(clip_paths), describe_store(store)))
    elif clips_enabled(cfg) and args.no_clips:
        print("clips     : skipped (--no-clips)")
    if start_utc is None:
        print("wall-clock: unknown -- start_utc/end_utc left empty (pass --source-start-utc)")
    else:
        print("wall-clock: origin %s" % args.source_start_utc or start_utc)

    # Pitfall 14.8: if background sits near action, no threshold will save it.
    if parts.get("pose") is not None:
        print("\nfusion weights:")
        print(describe_fusion(cfg, class_names))

    stats = background_score_stats(grid)
    units = "sigma above background" if cfg.get("normalize_scores", True) else "raw cosine"
    print("\nscore distribution over all windows and classes (%s):" % units)
    print("  min %.4f | p50 %.4f | p90 %.4f | p99 %.4f | max %.4f" % (
        stats["min"], stats["p50"], stats["p90"], stats["p99"], stats["max"]))
    thin = 1.0 if cfg.get("normalize_scores", True) else 0.05
    if stats["max"] - stats["p50"] < thin:
        print("  WARNING: background and action scores are nearly indistinguishable.")
        print("  No threshold will fix this -- see spec 11 (Phase 5).")

    for det in detections:
        clip = clip_paths.get(det["event_id"])
        print("  %-20s %8.2fs -> %8.2fs  (%.1fs)  score %.4f%s" % (
            det["class"], det["start"], det["end"], det["end"] - det["start"], det["score"],
            "  " + clip if clip else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())

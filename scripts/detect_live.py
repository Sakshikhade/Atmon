#!/usr/bin/env python3
"""Live camera detection (spec 10).

Requires a calibrated tau_high: live detection can only consume a threshold, it
cannot produce one, because the sweep needs cached offline features over a
labeled eval set. Run calibrate.py first.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config, require_tau_high  # noqa: E402
from src.encoder import build_encoder  # noqa: E402
from src.live import run_live  # noqa: E402
from src.prototypes import load_bank  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--camera", type=int, default=None, help="override live.camera_index")
    parser.add_argument("--max-seconds", type=float, default=None, help="stop after N seconds")
    parser.add_argument("--list-cameras", action="store_true",
                        help="probe camera indices and exit")
    args = parser.parse_args()

    if args.list_cameras:
        from src.live import list_cameras

        print("probing cameras (a device can open and still never deliver a frame):")
        for cam in list_cameras():
            print("  index %d : %dx%d @%.0ffps  %s"
                  % (cam["index"], cam["width"], cam["height"], cam["fps"],
                     "DELIVERS FRAMES" if cam["delivers"] else "opens but no frames"))
        print("\nmacOS lists Continuity Camera (your iPhone) alongside the built-in one.")
        print("Pick a working index with --camera N, or set live.camera_index.")
        return 0

    bank_cfg = load_config(args.config)
    bank, w_base_sec = load_bank(bank_cfg)
    cfg = load_config(args.config, w_base_sec=w_base_sec)
    tau_high = require_tau_high(cfg)

    if args.camera is not None:
        cfg["live"]["camera_index"] = args.camera
    print("camera    : index %d" % cfg["live"]["camera_index"])

    encoder = build_encoder(cfg, batch_size=1)
    print("device    : %s (autocast=%s)" % (encoder.device, encoder.autocast_dtype or "off"))

    def on_event(kind, event):
        if kind == "open":
            print("  [open ] %-20s start %8.2fs  score %.4f" % (
                event["class"], event["start"], event["score"]))
        elif kind == "close":
            forced = "  (FORCED -- see spec 9.4)" if event.get("forced") else ""
            clip = event.get("clip_path")
            print("  [close] %-20s %8.2fs -> %8.2fs  score %.4f%s%s" % (
                event["class"], event["start"], event["end"], event["score"], forced,
                "\n          clip: " + clip if clip else ""))
        elif kind == "discarded":
            print("  [short] %-20s %8.2fs -> %8.2fs  below min duration; open row stands" % (
                event["class"], event["start"], event["end"]))

    run_live(cfg, encoder, bank, tau_high, on_event=on_event, max_seconds=args.max_seconds)
    print("event log : %s" % cfg.path(cfg["event_log"]["path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())

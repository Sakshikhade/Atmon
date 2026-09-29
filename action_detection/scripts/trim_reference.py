#!/usr/bin/env python3
"""Trim a reference clip to the span that actually contains the action.

Reference duration is load-bearing twice over (spec 6, 7.1): it sets W_base,
which sets window length and the minimum detectable duration, and any neutral
frames left in the clip get baked into the prototype and pull it toward
background.

The original is moved to <class_dir>/raw/ rather than deleted. build_bank only
globs files directly in the class directory, so the raw copy is ignored.
"""

import argparse
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def trim(src, start, end, dest):
    import cv2

    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise SystemExit("could not open %s" % src)
    fps = cap.get(cv2.CAP_PROP_FPS)
    size = (int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)))
    writer = cv2.VideoWriter(dest, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    if not writer.isOpened():
        raise SystemExit("could not open a writer for %s" % dest)

    kept = i = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            t = i / fps
            if start <= t <= end:
                writer.write(frame)
                kept += 1
            i += 1
    finally:
        writer.release()
        cap.release()
    return kept, fps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clip", required=True, help="reference clip to trim")
    parser.add_argument("--start", type=float, required=True)
    parser.add_argument("--end", type=float, required=True)
    parser.add_argument("--min-duration", type=float, default=2.0,
                        help="floor from frames_per_clip/working_fps (spec 6)")
    args = parser.parse_args()

    span = args.end - args.start
    if span < args.min_duration:
        raise SystemExit(
            "trimmed span is %.2fs, below the %.2fs floor -- shorter clips are padded\n"
            "with repeated frames while target chunks are not (spec 6)."
            % (span, args.min_duration)
        )

    directory = os.path.dirname(os.path.abspath(args.clip))
    name = os.path.basename(args.clip)
    raw_dir = os.path.join(directory, "raw")
    os.makedirs(raw_dir, exist_ok=True)

    tmp = os.path.join(directory, ".trim_tmp.mp4")
    kept, fps = trim(args.clip, args.start, args.end, tmp)

    shutil.move(args.clip, os.path.join(raw_dir, name))
    final = os.path.join(directory, os.path.splitext(name)[0] + "_trim.mp4")
    shutil.move(tmp, final)

    print("%s" % name)
    print("  kept %.2fs-%.2fs  (%d frames @ %.1ffps = %.2fs)"
          % (args.start, args.end, kept, fps, kept / fps))
    print("  original -> %s" % os.path.join("raw", name))
    print("  trimmed  -> %s" % os.path.basename(final))
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Phase 1 -- encode a video's chunks into the feature cache (spec 5)."""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.chunker import probe_video, video_id_for  # noqa: E402
from src.config import load_config  # noqa: E402
from src.encoder import build_encoder  # noqa: E402
from src.features import cache_path, encode_video, load_cache  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--video", required=True)
    parser.add_argument("--force", action="store_true", help="re-encode even if cached")
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()

    cfg = load_config(args.config)
    video_id = video_id_for(args.video)

    native_fps, n_frames, duration = probe_video(args.video)
    print("video    : %s" % args.video)
    print("native   : %.2f fps, %d frames, %.1fs" % (native_fps, n_frames, duration))
    print("working  : %.1f fps, %.2fs chunks" % (cfg["working_fps"], cfg["chunk_sec"]))

    started = time.time()
    if not args.force and load_cache(cfg, video_id) is not None:
        bundle = load_cache(cfg, video_id)
        print("cache    : HIT (%s)" % cache_path(cfg, video_id))
    else:
        encoder = build_encoder(cfg, batch_size=args.batch_size)
        print("device   : %s (autocast=%s)" % (encoder.device, encoder.autocast_dtype or "off"))
        bundle = encode_video(cfg, encoder, args.video, force=args.force, batch_size=args.batch_size)
        print("cache    : WROTE %s" % cache_path(cfg, video_id))

    elapsed = time.time() - started
    print("chunks   : %d, dim %d" % bundle["feats"].shape)
    print("elapsed  : %.2fs" % elapsed)
    print(
        "\nPhase 1 acceptance: run this again -- the second run must hit the cache\n"
        "and finish in well under a second."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Phase 2 -- build the prototype bank from reference clips (spec 6)."""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.config import load_config  # noqa: E402
from src.encoder import build_encoder  # noqa: E402
from src.prototypes import build_bank, save_bank, self_similarity  # noqa: E402

# Below this, the reference and target encoding paths have almost certainly
# diverged -- that is what Phase 2's acceptance test is for (spec 6, 14.1).
SELF_SIMILARITY_FLOOR = 0.9


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--references", default=None, help="override data/references/")
    parser.add_argument("--batch-size", type=int, default=4)
    args = parser.parse_args()

    cfg = load_config(args.config)
    print("model  : %s" % cfg["model_id"])
    print("classes: %s\n" % ", ".join(cfg.class_names))

    # Pose templates FIRST: they are cheap, and a pose failure should surface
    # before the encoder is loaded and the bank is built, not after.
    if cfg.get("pose", {}).get("enabled", False):
        print("Pose templates (spec 11.3):")
        try:
            from src.pose import build_pose_templates, save_pose_templates

            templates = build_pose_templates(cfg)
            print("  pose templates -> %s" % save_pose_templates(cfg, templates))
        except Exception as exc:  # noqa: BLE001
            print("  FAILED: %s" % exc)
            print("  The embedding stream still works; fix this before relying on fusion.")
        print()

    if cfg.get("hands", {}).get("enabled", False):
        print("Hand templates (finger configuration):")
        try:
            from src.hands import build_hand_templates, save_hand_templates

            templates = build_hand_templates(cfg)
            if templates:
                print("  hand templates -> %s" % save_hand_templates(cfg, templates))
            else:
                print("  no hands detected in any reference clip -- stream unavailable")
        except Exception as exc:  # noqa: BLE001
            print("  FAILED: %s" % exc)
        print()

    encoder = build_encoder(cfg, batch_size=args.batch_size)
    print("device : %s (autocast=%s)\n" % (encoder.device, encoder.autocast_dtype or "off"))

    bank, w_base_sec = build_bank(cfg, encoder, args.references)
    out = save_bank(cfg, bank, w_base_sec)

    print("\nprototype bank -> %s" % out)
    print("W_base (median reference duration): %.2fs" % w_base_sec)

    print("\nPhase 2 acceptance -- self-similarity should be near 1.0:")
    failures = []
    for name in sorted(bank):
        score = self_similarity(bank, name)
        flag = "" if score >= SELF_SIMILARITY_FLOOR else "   <-- TOO LOW"
        if flag:
            failures.append(name)
        print("  %-24s %d variants   %.4f%s" % (name, len(bank[name]), score, flag))

    if failures:
        print(
            "\nFAIL: %s scored below %.2f. The reference and target encoding paths\n"
            "have diverged -- check frame count, resize, normalization and pooling\n"
            "before trusting any detection (spec 6, pitfall 14.1)."
            % (", ".join(failures), SELF_SIMILARITY_FLOOR)
        )
        return 1

    print("\nOK. Next: scripts/check_separation.py, then scripts/calibrate.py.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Config loading and derived values.

All scripts read the same config.yaml (spec 13). tau_high is the one value that
is never hand-edited: it comes from calibrate.py, either patched into the yaml
or left in cache/calibration.json (spec 12).
"""

import json
import os

import yaml

CALIBRATION_PATH = "cache/calibration.json"


class Config(dict):
    """dict with attribute access and the derived values the pipeline needs."""

    def __getattr__(self, key):
        try:
            return self[key]
        except KeyError:
            raise AttributeError(key)

    @property
    def root(self):
        return self["_root"]

    def path(self, *parts):
        """Resolve a config-relative path against the project root."""
        return os.path.join(self.root, *parts)

    @property
    def class_names(self):
        return sorted(self["classes"].keys())

    def class_cfg(self, name):
        return self["classes"].get(name, {})

    @property
    def stride_chunks(self):
        """Default S = max(1, W//4) is expressed here as stride_ratio."""
        return max(1, int(round(self["w_base_chunks"] * self["stride_ratio"])))

    @property
    def w_base_chunks(self):
        return self["w_base_chunks"]

    def window_lengths_chunks(self):
        """Window lengths in chunks, one per scale, deduplicated and >= 1."""
        base = self["w_base_chunks"]
        lengths = sorted({max(1, int(round(base * s))) for s in self["window_scales"]})
        return lengths


def _require(cfg, key):
    if key not in cfg:
        raise ValueError("config.yaml is missing required key %r" % key)


def load_config(path="config.yaml", w_base_sec=None):
    """Load config.yaml.

    w_base_sec (median reference clip duration, spec 7.1) is written by
    build_prototypes.py into the prototype bank; pass it here so window lengths
    can be derived. Without it, w_base defaults to one chunk and multi-scale
    pooling degenerates -- callers that score must supply it.
    """
    path = os.path.abspath(path)
    root = os.path.dirname(path)
    with open(path, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    for key in ("working_fps", "chunk_sec", "window_scales", "classes"):
        _require(raw, key)

    raw.setdefault("backbone", "vjepa")
    raw.setdefault("backbones", {})
    # model_id names the ACTIVE backbone's checkpoint. It flows into the event
    # log and the feature cache, so it has to follow the backbone selection
    # rather than sit beside it.
    active = raw["backbones"].get(raw["backbone"]) or {}
    if active.get("model_id"):
        raw["model_id"] = active["model_id"]
    _require(raw, "model_id")

    raw.setdefault("stride_ratio", 0.25)
    raw.setdefault("topk_prototypes", 3)
    raw.setdefault("smoothing_windows", 3)
    raw.setdefault("tau_low_ratio", 0.85)
    raw.setdefault("min_duration_ratio", 0.5)
    raw.setdefault("nms_tiou", 0.5)
    raw.setdefault("max_false_alarms_per_hour", 5)
    if active.get("frames_per_clip"):
        raw["frames_per_clip"] = active["frames_per_clip"]
    raw.setdefault("frames_per_clip", 64)
    raw.setdefault("device", "auto")
    raw.setdefault("dtype", "auto")
    raw.setdefault("video_backend", "auto")
    raw.setdefault("event_log", {})
    raw.setdefault("live", {})
    raw.setdefault("prototypes", {})
    raw.setdefault("clips", {})
    raw.setdefault("pose", {})
    raw.setdefault("normalize_scores", True)

    raw.setdefault("crop", {})
    raw.setdefault("hands", {})
    raw["crop"].setdefault("enabled", False)
    raw["crop"].setdefault("padding", 0.35)
    raw["crop"].setdefault("min_size", 0.25)
    raw["crop"].setdefault("smooth_window", 5)
    raw["hands"].setdefault("enabled", False)
    raw["hands"].setdefault("model_path", None)
    raw["hands"].setdefault("min_confidence", 0.4)
    raw["hands"].setdefault("temperature", 0.35)

    raw["pose"].setdefault("enabled", False)
    raw["pose"].setdefault("model_path", None)
    raw["pose"].setdefault("min_confidence", 0.5)
    raw["pose"].setdefault("temperature", 0.35)

    # Clip saving defaults OFF when the block is absent: writing video of the
    # monitored person is opt-in, never something a missing config key turns on.
    raw["clips"].setdefault("enabled", False)
    raw["clips"].setdefault("dir", "data/clips")
    raw["clips"].setdefault("pre_roll_sec", 2.0)
    raw["clips"].setdefault("post_roll_sec", 2.0)
    raw["clips"].setdefault("max_clip_sec", 60)
    raw["clips"].setdefault("max_total_gb", 5.0)
    raw["clips"].setdefault("jpeg_quality", 80)

    raw["event_log"].setdefault("path", "data/events/events.csv")
    raw["event_log"].setdefault("flush_each_event", True)
    raw["event_log"].setdefault("max_open_sec", 300)
    raw["event_log"].setdefault("infer_video_start_from_mtime", False)

    raw["live"].setdefault("camera_index", 0)
    raw["live"].setdefault("capture_fps", 30)
    raw["live"].setdefault("drop_on_backpressure", True)
    raw["live"].setdefault("camera_warmup_sec", 5.0)
    raw["live"].setdefault("cross_class_resolution", True)
    raw["live"].setdefault("cross_class_on_raw", True)
    raw["live"].setdefault("protect_background", True)
    raw["live"].setdefault("mutual_exclusion", True)
    raw["live"].setdefault("centre_warmup_chunks", 5)
    raw["live"].setdefault("use_calibrated_scale", False)
    raw["live"].setdefault("background_warmup_chunks", 15)
    raw["live"].setdefault("background_window_chunks", 300)

    raw["prototypes"].setdefault("variants_per_class", 12)
    raw["prototypes"].setdefault("speeds", [0.8, 1.0, 1.25])
    raw["prototypes"].setdefault("temporal_crop_min_ratio", 0.7)
    raw["prototypes"].setdefault("spatial_crop_min_ratio", 0.9)
    raw["prototypes"].setdefault("degrade", True)
    raw["prototypes"].setdefault("seed", 0)

    raw["_root"] = root
    raw["_config_path"] = path

    # tau_high: yaml value wins; else the calibration sidecar; else None.
    if raw.get("tau_high") is None:
        sidecar = os.path.join(root, CALIBRATION_PATH)
        if os.path.exists(sidecar):
            with open(sidecar, "r", encoding="utf-8") as fh:
                raw["tau_high"] = json.load(fh).get("tau_high")
            raw["_tau_high_source"] = CALIBRATION_PATH
    else:
        raw["_tau_high_source"] = "config.yaml"

    chunk_sec = float(raw["chunk_sec"])
    if w_base_sec is None:
        raw["w_base_sec"] = chunk_sec
        raw["w_base_chunks"] = 1
    else:
        raw["w_base_sec"] = float(w_base_sec)
        raw["w_base_chunks"] = max(1, int(round(float(w_base_sec) / chunk_sec)))

    return Config(raw)


def require_tau_high(cfg):
    """Detection needs a calibrated threshold. Never invent one (spec 14.5)."""
    if cfg.get("tau_high") is None:
        raise SystemExit(
            "tau_high is not set. Run scripts/calibrate.py to sweep it against a\n"
            "labeled eval set -- it must not be hand-picked (spec 8, pitfall 14.5)."
        )
    return float(cfg["tau_high"])


def save_calibration(cfg, tau_high, extra=None):
    """Persist the calibrated threshold to the sidecar read by load_config()."""
    payload = {"tau_high": float(tau_high)}
    payload.update(extra or {})
    out = cfg.path(CALIBRATION_PATH)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    return out


def patch_config_tau_high(cfg, tau_high):
    """Rewrite only the tau_high line of config.yaml, preserving comments.

    A yaml round-trip would drop every comment in the file, so this edits the
    single line instead.
    """
    path = cfg["_config_path"]
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    for i, line in enumerate(lines):
        if line.startswith("tau_high:"):
            lines[i] = "tau_high: %.6f            # set by calibrate.py, never hand-edited\n" % tau_high
            break
    else:
        raise ValueError("no top-level 'tau_high:' line found in %s" % path)

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    return path


def add_class_to_config(cfg, class_name, allow_flip=False):
    """Register a new class in config.yaml's `classes:` block, preserving
    comments (see patch_config_tau_high -- a yaml round-trip would drop them).

    `classes:` keys are the single source of truth for which behaviours exist
    (build_bank, calibrate.py, live detection all iterate cfg.class_names, not
    the data/references/ directory listing) -- config.yaml says so directly:
    "Directory names under data/references/ must match these keys exactly." A
    reference clip recorded into a directory that never gets a matching key
    here is invisible to the whole pipeline: encoded into no bank, tracked by
    no HysteresisTracker, detected nowhere. No-ops if already present.
    """
    if class_name in cfg["classes"]:
        return cfg["_config_path"]

    path = cfg["_config_path"]
    with open(path, "r", encoding="utf-8") as fh:
        lines = fh.readlines()

    start = None
    for i, line in enumerate(lines):
        if line.startswith("classes:"):
            start = i
            break
    if start is None:
        raise ValueError("no top-level 'classes:' line found in %s" % path)

    # The block runs until the next top-level (unindented, non-comment,
    # non-blank) key, or EOF.
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = i
            break

    block = [
        "  %s:\n" % class_name,
        "    allow_flip: %s\n" % ("true" if allow_flip else "false"),
        "\n",
    ]
    lines[end:end] = block

    with open(path, "w", encoding="utf-8") as fh:
        fh.writelines(lines)
    return path

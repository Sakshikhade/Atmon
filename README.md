# Few-Shot Action Detection

Implementation of [proj_desc.md](proj_desc.md). Frozen V-JEPA 2 encoder plus
nearest-prototype matching: one reference clip per action class, and every
occurrence of those actions is found in a longer video or a live camera feed.
No training in v1.

## Status

| Spec | What | State |
| :--- | :--- | :--- |
| Phase 1 (§5) | Chunk encoding with cache | built |
| Phase 2 (§6) | Prototype bank | built |
| Phase 3 (§7) | Scoring and grouping | built |
| Phase 4 (§8) | Evaluation and calibration | built |
| §9 | Event log | built, **tests pass** |
| §9.6 | Per-event clip saving | built, retention **tests pass** |
| §10 | Live capture | built |
| §11.3 | Pose stream (DTW on keypoints) | built, **disabled** — harmful held out |
| — | Hand stream (21 finger landmarks) | built, **disabled** — harmful held out |
| — | Pose-derived crop | built, **disabled** — no-op on all footage tested |
| — | Backbone swap (X-CLIP) | **default**, mAP@0.5 0.750 held out |
| Phase 5 (§11) | Periodicity / linear head / pose / localizer | **not built** — gated behind "only if Phase 4 falls short" |

## Verification status — read this before trusting anything

**127 tests passing** (`python -m pytest tests/ -q`). Everything except the
encoder and video I/O is covered — pooling, similarity, hysteresis, NMS,
event-level metrics, threshold selection, the event log, clip retention, and the
full offline and live paths driven end to end against the stub encoder.

**Held-out result.** X-CLIP appearance, calibrated on eval1+eval2 and tested on
eval3 — 9.3 minutes, a room no reference has seen, 74% background, including a
64-second hand-on-chin hard negative:

```
P 1.00   R 0.75   mAP@0.5 0.750   FA/h 0.0   tp/fp/fn 3/0/1
```

Three of four events at tIoU 0.77–0.96, no false positives, nothing fired on the
chin rest. spec §13 expects 55–68 mAP@0.5 for long well-separated actions.

The same configuration on V-JEPA 2 scores **0.000**. See [PLAN.md](PLAN.md) §5.

**Encoder speed on an 8 GB M3:** X-CLIP 0.36 s/clip, SigLIP 0.16, V-JEPA 1.68.
At `chunk_sec: 2.0`, X-CLIP is ~0.18× real time, so live capture has real
headroom for the first time.

Still uncovered, and the most likely places for a first bug:

- **Video encode/decode.** `write_frames`, `extract_clips_from_video`,
  `FrameRetentionBuffer`, and the `chunker.py` backend chain need OpenCV and,
  for the live path, a camera. Nothing has exercised them.

## Measured stream ablation

`scripts/ablate.py` runs every stream/crop combination over the labelled eval set
and reports chunk-level **d'** and **AUROC** — diagnostic instruments for "can
the representation see this behaviour", never the reported metric (that stays
event-level, spec §14.4).

On eval1 (60s, 5 instances — thin, treat as directional):

```
setup              ear_cover            hair_twirling
vjepa only         d' +2.50 AUC 0.97    d' +0.49 AUC 0.58
+ pose             d' +2.60 AUC 0.96    d' +0.53 AUC 0.60
+ hands            d' +2.50 AUC 0.97    d' +1.46 AUC 0.83
+ pose + hands     d' +2.60 AUC 0.96    d' +1.19 AUC 0.73
```

**The hand stream is what rescues hair_twirling** (0.58 -> 0.83 AUROC). Pose
alone barely moves it and, mixed back in at weight 0.25, actively drags it down
— pose has no finger landmarks, so for a finger behaviour it contributes noise.
Class weights now reflect that: hair_twirling is hands-dominant with pose at
zero, ear_cover stays pose-leaning.

**Cropping is a no-op on this footage** and correctly so: the subject spans the
whole frame, so there is nothing to crop away. It stays enabled because the
deployment case — a room camera where the subject is small in frame — is exactly
where it pays, and that case is untested here.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
```

Runs on CUDA, Apple Silicon (MPS), or CPU — `select_device()` picks in that
order. Autocast is bf16 on CUDA where supported, fp16 otherwise, and **fp32 on
MPS**, where fp16 autocast is unreliable across torch versions and silently
degrades similarity.

The active backbone (`backbone: xclip` in `config.yaml`, `microsoft/xclip-base-patch16`,
~750 MB) downloads automatically into the default Hugging Face cache
(`~/.cache/huggingface/hub`) the first time anything encodes a clip. To fetch it
up front instead of waiting on the first run:

```bash
python3 -c "from transformers import AutoProcessor, XCLIPModel; m='microsoft/xclip-base-patch16'; AutoProcessor.from_pretrained(m); XCLIPModel.from_pretrained(m)"
```

Requires internet access once; nothing else to configure — the cache location
is not overridden anywhere in this repo.

## Reference clip duration

**At least 2.0 s with the shipped config, and no longer than the shortest
instance you want to detect.** Both bounds are load-bearing:

- **Floor** = `frames_per_clip / working_fps` = 16 / 8 = **2.0 s**. Everything is
  resampled to a fixed frame count before encoding, so clips below that are
  padded with repeated frames while every target chunk is not. Similarity then
  degrades for reasons unrelated to the action. `build_prototypes.py` warns.
- **Ceiling** = the shortest real occurrence. The median reference duration
  becomes `W_base`, which sets window length, and a window longer than the action
  mixes in background (spec §7.1).

So: 2–4 s, trimmed to the action itself.

## Run order

```bash
python scripts/build_prototypes.py --config config.yaml
python scripts/calibrate.py        --config config.yaml --labels data/labels/
python scripts/detect.py           --config config.yaml --video data/videos/x.mp4 --out dets.json
python scripts/detect_live.py      --config config.yaml
```

Prototypes first (nothing scores without them), then calibration (nothing
detects without a `tau_high`), then detection. `detect.py` encodes into the
cache automatically when it is missing. `detect_live.py` refuses to run
uncalibrated: live detection can only consume a threshold, never produce one,
because the sweep needs cached offline features over a labeled eval set.

## Where detections go

Both `detect.py` and `detect_live.py` append to `data/events/events.csv`
unconditionally — there is no flag to turn it off.

- **Offline** writes one `closed` row per detection.
- **Live** writes an `open` row when a detection starts and a second `closed`
  row, sharing the `event_id`, when it ends. Read the log with
  `event_log.read_events()`, which groups by `event_id` and takes the last row.
- An event whose final row is still `open` means the run ended — cleanly or by
  crash — with that detection running. `event_log.unclosed_events()` finds them.
  They have a real start and no end; do not read `end_sec` as zero.
- `start_utc`/`end_utc` are **empty** unless the wall-clock origin is known.
  Live always knows it. A recorded file does not, so pass `--source-start-utc`
  or set `infer_video_start_from_mtime`. Nothing fabricates a datetime.

### Saved clips

`clips.enabled: true` in the shipped config writes an mp4 per detected event to
`data/clips/<source_id>/<event_id>.mp4`, and the path goes into the log's
`clip_path` column. Offline clips come from the source file at native fps and
resolution; live clips come from the retention buffer at `working_fps`, so they
are choppier — those are the only frames that path ever kept.

**This is the only thing in the system that writes video of the monitored person
to disk.** Everything else works on embeddings and timestamps. Set
`clips.enabled: false` for timestamps-only operation, which retains no video
anywhere, in memory or on disk. It defaults to false when the config block is
missing entirely, so a missing key can never silently turn recording on.

Retention deletes oldest-first past `max_total_gb` (5 GB), and clips longer than
`max_clip_sec` (60 s) are truncated. Log rows are append-only and never
rewritten, so **a `clip_path` can outlive its file** — check it exists before
opening. Live clip capture also means holding a rolling ~10 s window of
JPEG-compressed frames in RAM continuously, before any detection exists, because
otherwise the seconds before a detection are already gone by the time it fires.

### Timestamps under load

Live chunks are timestamped from **when their frames were actually captured**,
not from a chunk counter. On hardware that cannot encode as fast as the camera
delivers — which includes this MacBook — a counter would report every event as
having happened progressively earlier than it did. What degrades under load
instead is chunk *spacing*: chunks come to span uneven, longer stretches than
`chunk_sec`, and window pooling assumes uniform spacing, so scoring is distorted
while timestamps stay honest. `detect_live.py` warns the first time a chunk takes
longer to encode than it covers, and prints the mean encode time and real-time
factor when the session ends.

## You have no data yet

`data/references/`, `data/videos/` and `data/labels/` are empty, so nothing real
can be run end to end. `tests/fixtures.py` and `tests/stub_encoder.py` stand in:
synthetic clips of a moving square, and a numpy-only encoder exposing the same
`encode_clips` surface as the real one. `tests/test_pipeline.py` drives the real
prototype, scoring, grouping, and event-log code over them — only the encoder is
swapped.

To get to a real run you need, at minimum: one reference clip per class in
`data/references/<class>/`, and for Phase 4 a labeled eval set — the spec asks
for ≥20 instances across ≥30 minutes including plenty of background.
`calibrate.py` warns when you are under that but will still run; a threshold
calibrated on less is not trustworthy.

## The shipped config is tuned for 8 GB Apple Silicon

`working_fps: 8`, `chunk_sec: 2.0`, `frames_per_clip: 16`.

The spec's defaults (`chunk_sec: 1.0`, the model's native 64 frames) do not fit
here. ViT-L at 64 frames × 256px is ~8,200 tokens per clip; the weights alone are
1.2 GB in fp32 and the attention activations on top will OOM or swap against 8 GB
of unified memory. At 16 frames it is ~2,000 tokens — roughly 4× fewer, with
attention ~16× cheaper.

The chosen numbers multiply out exactly: 8 fps × 2.0 s = **16 frames per chunk**,
so `fit_clip_length()` resamples nothing and the encoder sees real frames rather
than repeats.

**The cost:** 16 frames is off-distribution for a model trained at 64, so
absolute similarity quality will be worse than the paper's. On a CUDA box with
real VRAM, set `frames_per_clip: 64` with `chunk_sec: 8.0` (or `working_fps: 32`)
and re-run `calibrate.py`. The cache keys on all three values, so changing them
invalidates cleanly rather than silently reusing stale features.

## Deviations from the spec

Four, all additive:

- **`src/config.py`** — not in the §3 layout. All five scripts read one config;
  something had to load it.
- **`frames_per_clip` in the feature cache** — the §4 contract does not list it,
  but changing it changes every vector while `model_id`, `working_fps` and
  `chunk_sec` all stay put. That is exactly the silent-stale-cache failure
  pitfall §14.6 warns about, so it is stored and keyed on.
- **`__w_base_sec__` in the prototype bank** — `W_base` comes from the median
  reference duration, which is known at build time and needed at scoring time.
  Class names starting with `__` are rejected so it cannot collide.
- **`cache/calibration.json`** — `calibrate.py` writes the threshold here and
  `load_config` reads it when `tau_high` is null, so the value comes from the
  sweep without a yaml round-trip destroying every comment in `config.yaml`.
  `--write-config` also patches the single `tau_high:` line in place.

## Layout

```
config.yaml              # every script reads this one file
src/
  config.py              # loading, derived values (W_base, window lengths)
  encoder.py             # V-JEPA 2 wrapper; THE single encoding path
  chunker.py             # video -> non-overlapping chunks, 4 decoder backends
  features.py            # chunk encoding + on-disk cache
  prototypes.py          # reference clips -> augmented prototype bank
  scoring.py             # pooling, top-k similarity, hysteresis, NMS
  evaluate.py            # event-level metrics, threshold sweep
  live.py                # camera capture + online grouping
  event_log.py           # durable CSV log (stdlib only, by design)
  clip_writer.py         # per-event clip extraction, retention buffer, budget
scripts/                 # the five CLI entry points
tests/                   # fixtures.py + stub_encoder.py + four test modules
```

One structural note: the encoder is reached through `Encoder.encode_clips()` and
nowhere else, by references, offline chunks, and live chunks alike. The spec
names a divergence between those paths as the most common silent failure in the
system (§14.1, §14.9), so there is exactly one function that resamples,
preprocesses, pools and normalizes, and no way around it.

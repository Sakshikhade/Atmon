# Few-Shot Action Detection

Frozen encoder + nearest-prototype matching: teach each action with a short
reference clip, then find every occurrence in a longer video or a live feed.
No training in v1. Spec: [proj_desc.md](proj_desc.md). Measurement history:
[PLAN.md](PLAN.md).

**Default backbone:** X-CLIP (`microsoft/xclip-base-patch16`).

Before trusting any number this produces, read
[Verification status](#verification-status--read-this-before-trusting-anything).
**The threshold this repo ships with is an uncalibrated demo preset.**

## Quick start — web demo

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# Frontend (React). Rebuild after UI changes: cd webapp/ui && npm install && npm run build
# Output lands in webapp/static/ (what FastAPI serves).
python webapp/server.py
# open http://127.0.0.1:8000  (HOST / PORT env vars override)
#
# Optional hot reload while developing UI:
#   terminal A: python webapp/server.py
#   terminal B: cd webapp/ui && npm run dev   # http://127.0.0.1:5173 proxies /api
```

Flow: **References** (record or upload a 2–4 s clip per class → bank rebuilds) →
**Live** (browser webcam) → **Captures** (saved detection clips).

Live capture uses **this device’s browser webcam** by default (`getUserMedia` →
JPEG frames → `/api/frame`). That is required on headless hosts (e.g. EC2) that
have no `/dev/video*`. Server-side OpenCV cameras remain available via
`source=server` for local CLI / machines with a physical cam.

### Pose landmarker (ear gate + skeleton overlay)

MediaPipe `pose_landmarker.task` is required for:
- the **ear_cover wrist-near-ear gate** (live and offline)
- the live **skeleton overlay** (`overlay.enabled: true`)

It is **not** the DTW pose score stream (`pose.enabled` stays false).

```bash
mkdir -p models
# Download MediaPipe Pose Landmarker (lite/full) as:
#   models/pose_landmarker.task
# Or set pose.model_path in config.yaml.
# *.task files are gitignored — supply them per machine / deploy.
```

Without the file, live sessions that need the wrist gate abort with a clear
error; offline detect continues appearance-only and warns that the gate is
unguarded. Overlay-only failures degrade softly in the web demo.

### EdgeFace (planned — not wired yet)

[Idiap EdgeFace-XS-GAMMA](https://huggingface.co/Idiap/EdgeFace-XS-GAMMA)
(`edgeface_xs_gamma_06`, ~1.77M params) is downloaded locally for a future
face-embedding path. **It is not used by detection today.**

```bash
mkdir -p models
# From the Hugging Face repo Idiap/EdgeFace-XS-GAMMA:
#   models/edgeface_xs_gamma_06.pt
#   models/edgeface_xs_gamma_06_checksum.txt   # MD5 ebf343ca6930bd4d7cfea256cf14d703
#
# Or with huggingface_hub:
#   python -c "from huggingface_hub import hf_hub_download; \
#     hf_hub_download('Idiap/EdgeFace-XS-GAMMA','edgeface_xs_gamma_06.pt',local_dir='models')"
```

**Requirements (when we wire it):**
- Weights: `models/edgeface_xs_gamma_06.pt` (gitignored; ~6.8 MB)
- Runtime: PyTorch + a face-align preprocess (model card uses
  `face_alignment.align` + normalize mean/std `0.5`)
- Architecture code from the EdgeFace project (checkpoint alone is not enough)
- License: [CC BY-NC-SA 4.0](https://huggingface.co/Idiap/EdgeFace-XS-GAMMA) —
  non-commercial; keep that constraint in mind for any deployment

Do **not** commit the `.pt` binary.

### Multi-user subjects (Active Subject + EdgeFace)

When `identity.enabled: true` (shipped default), the demo is **multi-user on one
device**:

1. Create / select a **subject** in the web UI (or `--subject-id` on CLI).
2. Record action reference clips **for that subject** → bank + face gallery are
   harvested from those clips (no separate face-enroll step).
3. Live only opens detections when the camera face matches that subject’s
   gallery (`match_threshold` is an **UNCALIBRATED** demo preset).

This is an explicit exception to the earlier “no person re-ID” non-goal in
[proj_desc.md](proj_desc.md) §1 — identity is a session gate, not action fusion.

Layout: `data/subjects/<id>/{meta.json,references/,face/}`. Legacy
`data/references/` migrates once into subject `default`.

## Status

| Area | State |
| :--- | :--- |
| Chunk encoding + cache | built |
| Prototype bank | built |
| Scoring / grouping / hysteresis | built |
| Calibration + event metrics | built (code); **never run on real labels here** |
| Event log (§9) | built, tested |
| Per-event clips (§9.6) | built — **H.264 via PyAV** for browser playback |
| Live capture (§10) | built — CLI + web |
| Web demo (`webapp/`) | built — browser webcam, SSE events, gallery |
| Pose DTW fusion | built, **disabled** (harmful held out) |
| Hand DTW fusion | built, **disabled** (harmful held out) |
| Ear-cover wrist-near-ear gate | **on** (geometry only; does not enable DTW) |
| Hair-twirling open confirm | **3 s** sustained above τ before fire |
| Skeleton overlay on live preview | **on** (rendering only; does not enable DTW) |
| EdgeFace face embeddings | **on** (Active Subject gate; harvest-from-refs) |
| Multi-user subjects | **on** (select subject before Live / record) |
| Phase 5 (periodicity / linear head) | not built |

**219 tests passing** (`python -m pytest -q`).

## Verification status — read this before trusting anything

**The shipped threshold is not calibrated.** `cache/calibration.json` holds a
fixed 1.5σ demo preset, `config.yaml` has `tau_high: null`, and `data/labels/`
is empty. Spec §8 and pitfall §14.5 both forbid an eye-picked threshold. The UI
and both CLI entry points now say `UNCALIBRATED` wherever that threshold is
shown, and nothing but `scripts/calibrate.py` may write the sidecar — but the
number itself is still a guess until a real sweep runs.

Three further knobs are also hand-set from live observation, not swept, and
multiply the global τ before anything opens: `ear_cover.tau_scale: 1.8`,
`hair_twirling.tau_scale: 2.0`, and `hair_twirling.confirm_sec: 3.0`. Reset them
to 1.0 / 0.0 before any calibration run or the sweep optimizes one knob with two
eye-picked multipliers frozen inside its objective.

**What the tests do and do not cover.** Everything except the encoder and video
I/O is covered — pooling, similarity, hysteresis, NMS, event-level metrics,
threshold selection, the event log, clip retention, calibration provenance, the
wrist gate, and the full offline and live paths driven end to end against a stub
encoder.

Still uncovered, and the most likely places for a first bug:

- **Video encode/decode.** `write_frames`, `extract_clips_from_video`,
  `FrameRetentionBuffer`, and the `chunker.py` backend chain need OpenCV and,
  for the live path, a camera. Nothing has exercised them.
- **Most of the web layer.** Only four routes are exercised by tests
  (`/api/frame`, `/api/classes`, `/api/record/stop`, `/api/references/upload`)
  out of sixteen, and there is no frontend test runner at all — the frame pump,
  SSE handlers, and gallery rendering in the React UI are unexercised.

## Historical result (footage not in this repository)

X-CLIP appearance only, calibrated on eval1+eval2, tested on eval3 — 9.3
minutes, a room no reference had seen, 74 % background, including a 64-second
hand-on-chin hard negative:

```
P 1.00   R 0.75   mAP@0.5 0.750   FA/h 0.0   tp/fp/fn 3/0/1
```

The same configuration on V-JEPA 2 scores **0.000**; SigLIP 0.500. See
[PLAN.md](PLAN.md) §5.

**This is not reproducible from this tree.** The eval footage is not here, and
there is no held-out scorer — `detect.py` writes `dets.json` but nothing reads
it. Read the numbers with PLAN.md §5's own caveats attached: four instances, so
recall moves in steps of 0.25, and FA/h 0.0 over seven minutes means "below
about 8/hour", not zero.

**Encoder speed on an 8 GB M3:** X-CLIP 0.36 s/clip, SigLIP 0.16, V-JEPA 1.68.
At `chunk_sec: 2.0`, X-CLIP is ~0.18× real time, so live capture has headroom.

## You do not have enough data yet

`data/references/` has exactly **one clip per class, all from one setting**.
`data/videos/` does not exist and `data/labels/` is empty, so no offline run,
no calibration, and no evaluation can happen here. `cache/features/` has never
been created — `detect.py` has never run against a file in this tree.

[PLAN.md](PLAN.md) §7 is explicit that data, not code, is the binding
constraint. Its gap table, against what is actually present:

| Need | Have |
| :--- | :--- |
| References from ≥2 settings per action | 1 setting |
| An eval video from a never-referenced setting | none |
| ≥10 min of background-heavy footage | none |
| ≥20 instances per class | none labeled |

`tests/fixtures.py` and `tests/stub_encoder.py` stand in: synthetic clips of a
moving square, and a numpy-only encoder exposing the same `encode_clips`
surface as the real one. `tests/test_pipeline.py` drives the real prototype,
scoring, grouping, and event-log code over them — only the encoder is swapped.
That verifies plumbing and says nothing about how the real backbone scores real
behaviour.

`calibrate.py` warns when you are under the spec's ≥20 instances / ≥30 minutes
and will still run. A threshold calibrated on less is not trustworthy.

## Live behaviour notes

- **Warmup:** ~15 background chunks (~30 s at `chunk_sec: 2.0`) before any class
  may open. The UI waits for `detector.ready`, not a bare chunk counter.
- **`hair_twirling.confirm_sec: 3.0`:** score must stay above that class’s τ for
  ~3 s; single-chunk spikes reset and do not open. Event start is stamped at the
  beginning of the confirming streak.
- **`hair_twirling.tau_scale: 2.0`:** effective open bar ≈ 3.0σ (global τ × scale).
- **`ear_cover`:** raised τ + wrist-near-ear gate (MediaPipe pose landmarker).
  The gate is evaluated per class against that class’s own
  `wrist_near_ear_threshold`, on both live and offline (`group_detections`).
  Needs `models/pose_landmarker.task` (see above).
- **Clips:** `pre_roll_sec: 1.0` / `post_roll_sec: 2.0`. Written as browser-playable
  H.264 (`libx264`); OpenCV’s `mp4v` fallback is not used when PyAV is available.
- **Backpressure:** frames are always dropped rather than queued — the encoder
  cannot keep up with a 30 fps camera and only needs `working_fps`. The drop
  rate is reported instead. `pose_frame` SSE messages are also dropped for a
  subscriber whose queue is backed up; detection events never are.

## Skeleton overlay

`overlay.enabled: true` draws the MediaPipe skeleton on the live preview. It is
**rendering only** and does not turn the measured-harmful DTW pose stream back
on: the landmarker is loaded with `templates = None`, so `active_streams` stays
`["vjepa"]`. `tests/test_overlay_stream_isolation.py` runs the live loop with
the overlay off and on and asserts the emitted detections are unchanged.

Pose is computed **per frame**, not per chunk. Per chunk it ran after the
X-CLIP encode, so landmarks describing `t ∈ [T, T+2]` only arrived at
`T+2+encode` — 2 to 3.5 s stale, which puts a drawn hand at an ear seconds
after the real one came down. Per frame costs the same: `landmarks_for_clip`
already ran `detect()` on all 16 frames of every 2 s chunk, which is the same
8/sec.

Measured on an 8 GB M3:

- `detect()` with a real subject in frame: **20.9 ms/frame** mean, p95 21.4,
  against a 125 ms budget (`1000 / working_fps`). Hence `stride: 1`.
- Live session: **7.2 Hz** sustained, median gap 126 ms — but **max 900 ms**
  across an X-CLIP encode, since the capture loop blocks there. `max_age_ms`
  must exceed that or the skeleton blanks once per chunk; 700 blinked, so the
  shipped value is **1200**.

The skeleton visibly steps at 8 Hz against a 30 fps video. That is the rate the
detector samples at. It holds between landmark arrivals rather than
interpolating — inventing joint positions no model produced is the wrong
property for an instrument. It draws white over a dark outline, switching to
the `#007aff` accent while any detection is open, and clears when the subject
leaves frame. The **Skeleton** button toggles it per browser (`localStorage`).

Coordinates are mirrored once, server-side, in `landmarks_to_overlay()`, and
the `live_started` payload carries both the mirror flag and the edge topology
so the page cannot drift from `UPPER_BODY`. The canvas takes the video's
intrinsic size and the same `object-fit: cover`, so registration holds on a
16:9 camera in the 4:3 slot without any JS cover arithmetic.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

Runs on CUDA, Apple Silicon (MPS), or CPU — `select_device()` picks in that
order. Autocast is bf16 on CUDA where supported, fp16 otherwise, and **fp32 on
MPS**.

The X-CLIP weights (~750 MB) download into the Hugging Face cache on first
encode. Prefetch:

```bash
python3 -c "from transformers import AutoProcessor, XCLIPModel; m='microsoft/xclip-base-patch16'; AutoProcessor.from_pretrained(m); XCLIPModel.from_pretrained(m)"
```

## Reference clips

**2–4 s of the action itself**, one directory per class under
`data/references/<class>/`.

- **Floor** ≈ `frames_per_clip / working_fps` (X-CLIP: **8 / 8 = 1.0 s**; prefer
  ≥2 s in practice). Shorter clips get padded and degrade similarity for the
  wrong reason.
- **Ceiling** = shortest real instance you care about. Median reference duration
  becomes `W_base`. Long takes are motion-trimmed (`max_reference_sec: 4.0`).
- `W_base` is the median across **every** reference of **every** class, and it
  sets both the window lengths and `min_duration`. One 4 s reference among 2 s
  ones shifts the minimum detectable duration for all classes.

Class names in `config.yaml` must match directory names
(`ear_cover`, `hair_twirling`, `head_nodding`).

## CLI run order

```bash
python scripts/build_prototypes.py --config config.yaml
python scripts/calibrate.py        --config config.yaml --labels data/labels/
python scripts/detect.py           --config config.yaml --video data/videos/x.mp4 --out dets.json
python scripts/detect_live.py      --config config.yaml   # server OpenCV camera
python scripts/detect_live.py --list-cameras              # pick live.camera_index
```

Prototypes first, then calibration (`tau_high`), then detection.

**The two entry points differ on an uncalibrated threshold, deliberately.**
`detect_live.py` calls `require_tau_high` and refuses to run without one. The
web demo runs anyway on an in-memory 1.5σ preset, so the record → detect loop
works before any labeling has happened — and labels every surface with
`UNCALIBRATED` while it does. The preset is never written to disk; only
`calibrate.py` writes `cache/calibration.json`.

## Where detections go

Both offline and live append to `data/events/events.csv`.

- **Offline:** one `closed` row per detection.
- **Live:** `open` row at start, second `closed` row with the same `event_id` at
  end. Use `event_log.read_events()` to resolve the latest row per id.
- **Clips** (when `clips.enabled: true`): `data/clips/<source_id>/<event_id>.mp4`,
  path stored in `clip_path`. Retention deletes oldest-first past `max_total_gb`.
  Log rows are append-only — a `clip_path` can outlive its file. That is the
  documented trade (§9.6, pitfall §14.15), and it is the state of this tree
  today: 32 `closed` rows, **zero** clip files. Do not read a `clip_path` as a
  promise the file exists; `/api/events` already skips rows whose clip is gone.

One event in the log has an `open` row and no `closed` row. Per §9.2 that means
the run ended while the detection was still running — `_live_loop` is a daemon
thread, and a daemon thread killed at interpreter exit never reaches its flush.
`max_open_sec` cannot help there: force-close happens inside
`HysteresisTracker.step()`, which needs a *subsequent* chunk that never comes.
The count is surfaced in the UI header and in `/api/state`, because a reader
that only counts `closed` rows silently loses these.

Live timestamps come from **capture time**, not a chunk counter, so under load
timestamps stay honest while chunk spacing may stretch.

## Config snapshot (shipped)

| Key | Value | Why |
| :--- | :--- | :--- |
| `backbone` | `xclip` | Held-out winner vs V-JEPA / SigLIP |
| `working_fps` / `chunk_sec` / `frames_per_clip` | 8 / 2.0 / 8 | Matches X-CLIP training length |
| `pose.enabled` / `hands.enabled` | false | Fusion hurt held-out mAP |
| `clips.pre_roll_sec` | 1.0 | Lead-in on saved clips |
| `live.background_warmup_chunks` | 15 | Avoid early noise opens |
| `live.open_margin` | 0.35 | Near-tie suppression (σ ranking) |
| `classes.hair_twirling.confirm_sec` | 3.0 | Sustained open gate — **hand-set** |
| `classes.ear_cover.require_wrist_near_ear` | true | Chin-rest FP cut |
| `overlay.enabled` | true | Skeleton on the preview; rendering only |
| `overlay.max_age_ms` | 1200 | Must exceed a 900 ms encode stall |

## Layout

```
config.yaml              # single source of truth for scripts + webapp
conftest.py / pytest.ini # repo root on sys.path; pins pytest rootdir
src/
  config.py              # load + derived values (W_base, windows, calibration_status)
  encoder.py             # THE single encoding path (refs / offline / live)
  chunker.py             # video → chunks (decord / PyAV / torchvision / OpenCV)
  features.py            # encode + on-disk cache
  prototypes.py          # reference clips → prototype bank
  scoring.py             # pool, top-k, hysteresis (+ confirm_sec), NMS
  evaluate.py            # event metrics, threshold sweep
  live.py                # CameraStream + LiveDetector
  pose.py / hands.py     # optional streams + ear wrist gate
  event_log.py           # CSV audit trail
  clip_writer.py         # H.264 clips, retention buffer, size budget
webapp/
  server.py              # FastAPI demo (browser webcam ingest)
  ui/                 # React (Vite) source — DESIGN.md + Apple HIG tool UI
  static/             # Built assets (npm run build); FastAPI serves this
scripts/                 # CLI entry points
tests/                   # stub encoder + pipeline / web / clip tests
```

The encoder is only reached through `Encoder.encode_clips()`. Divergent
reference / offline / live encode paths are the classic silent failure mode in
the spec (§14.1, §14.9) — there is one resample / preprocess / pool / L2 path.

## Deviations from the spec

- **`src/config.py`** — shared loader for all entry points.
- **`frames_per_clip` in the feature cache** — keyed so length changes cannot
  silently reuse stale vectors (§14.6). The prototype bank is keyed on the crop
  setting for the same reason.
- **`__w_base_sec__` in the prototype bank** — build-time median duration for
  scoring; `__`-prefixed names are reserved.
- **`cache/calibration.json`** — `calibrate.py` writes τ here; `load_config`
  reads it when `tau_high` is null without rewriting `config.yaml`, and carries
  the sidecar's own `source` string through so a preset cannot present itself
  as a measurement.
- **`webapp/`** — not in the §3 layout; wraps the same live detector for demos.
- **`confirm_sec`** — per-class open hold; additive to hysteresis (§7.3.2).

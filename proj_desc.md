# Few-Shot Action Detection — Project Specification

**Read this whole file before writing code.** Implement phases in order. Do not skip ahead to Phase 5.

Build order: Phases 1–4 (§5–§8), then the event log (§9) wired into Phase 3's grouping output, then live capture (§10), then Phase 5 (§11) only if Phase 4 fell short. The event log is written from the moment Phase 3 produces its first detection; live capture waits until Phase 4 has fixed a threshold.

---

## 1. Goal

Given one short trimmed reference clip per action class (2–5 classes total), find every occurrence of those actions in a longer untrimmed video, and output `(class, start_seconds, end_seconds, score)` for each detection.

**No training in v1.** The system is a frozen encoder plus nearest-prototype matching. Adding a new action class means adding one reference clip — nothing is retrained.

**Two input sources, one detection path.** Recorded video files (offline batch) and a live camera feed (streaming) are both v1 scope. They share the same encoder, prototypes, scoring, and grouping code; they differ only in how frames arrive and when an event's end time becomes known. Every detection from either source is persisted to the event log (§9), and — when clip saving is enabled — a video clip of the event is written alongside it (§9.6).

### Non-goals (do not implement)
- Person *tracking* or concurrent multi-person attribution. **Exception:** an
  Active-Subject face gate (EdgeFace) may enroll one user at a time and refuse
  opens when the camera face does not match that subject’s gallery. See
  `identity:` in config.yaml and README.
- Assume one *active* subject per Live session (or accept whatever is in frame
  when `identity.enabled: false`).
- Audio. Video only.
- Any fine-tuning, LoRA, or gradient updates.
- **Alerting or notification of any kind** — no webhooks, no SMS, no push, no sound. Detections are written to the event log and nothing else. Do not wire this pipeline to the `AlertGateway` in the parent AAMAS repository, now or later.

### Success criterion
Phase 4 produces an event-level precision/recall curve on a human-labeled eval set, and a chosen threshold with a stated false-alarms-per-hour rate. A system that runs but has never been measured is not done.

---

## 2. Environment

- Python 3.10+, PyTorch with CUDA, single GPU.
- `transformers`, `torch`, `numpy`, `decord` (video reading; fall back to `torchvision.io` or PyAV if decord is unavailable), `scikit-learn`, `pyyaml`, `tqdm`.
- `opencv-python` for live camera capture only. decord reads files, not devices — do not try to make it open a camera.
- Model: `facebook/vjepa2-vitl-fpc64-256` from HuggingFace, loaded via `transformers`. MIT licensed.
- Run the model in `eval()` mode under `torch.no_grad()`, fp16 or bf16 autocast.

---

## 3. Repository layout

```
.
├── config.yaml
├── src/
│   ├── encoder.py       # frozen V-JEPA 2 wrapper
│   ├── chunker.py       # video → non-overlapping chunks
│   ├── features.py      # chunk encoding + on-disk cache
│   ├── prototypes.py    # reference clips → prototype bank
│   ├── scoring.py       # pooling, similarity, grouping
│   ├── event_log.py     # durable CSV event log (§9)
│   ├── clip_writer.py   # per-event clip extraction + retention (§9.6)
│   ├── live.py          # camera capture + online grouping (§10)
│   ├── evaluate.py      # event-level metrics, threshold sweep
│   ├── pose.py          # Phase 5 only
│   └── periodicity.py   # Phase 5 only
├── scripts/
│   ├── build_prototypes.py
│   ├── encode_video.py
│   ├── detect.py
│   ├── detect_live.py
│   └── calibrate.py
├── data/
│   ├── references/<class_name>/*.mp4
│   ├── videos/*.mp4
│   ├── labels/<video_id>.json
│   └── events/events.csv        # append-only detection log
└── cache/features/<video_id>.npz
```

---

## 4. Data contracts

Define these early and do not deviate; every module depends on them.

**Chunk features** (`cache/features/<video_id>.npz`)
```
feats:      float32 [n_chunks, D]   L2-normalized
starts:     float32 [n_chunks]      chunk start time in seconds
chunk_sec:  float32 scalar
fps:        float32 scalar
model_id:   str                     for cache invalidation
```

**Prototype bank** (`cache/prototypes.npz`)
```
{class_name: float32 [n_variants, D]}   each row L2-normalized
```
Store all augmented variants. Do **not** collapse to a single mean vector.

**Ground truth labels** (`data/labels/<video_id>.json`)
```json
{"video_id": "clip_01", "duration": 1834.5,
 "events": [{"class": "hair_twirl", "start": 42.1, "end": 55.8}]}
```

**Detections** (output JSON) — same shape as `events`, plus a `score` float per event.

**Event log** (`data/events/events.csv`) — the durable record of every detection from every run, offline or live. Append-only, one header row, exactly these columns in this order:

```
run_id         str    uuid4, one per invocation of detect.py / detect_live.py
event_id       str    uuid4 per detection; stable across an event's open and closed rows
source_id      str    video_id for a file; session id (see §10) for a camera feed
source_type    str    "video" | "live"
status         str    "open" | "closed"
class          str
start_sec      float  seconds from source start (video t=0, or session start)
end_sec        float  seconds from source start; EMPTY on an "open" row
duration_sec   float  end_sec - start_sec; EMPTY on an "open" row
start_utc      str    ISO-8601 UTC; EMPTY when source_start_utc is unknown
end_utc        str    ISO-8601 UTC; EMPTY on an "open" row or when unknown
score          float  detection score; on an "open" row, the score at the opening window
model_id       str    ┐
working_fps    float  │ provenance — which config produced this row
chunk_sec      float  │
tau_high       float  ┘ the threshold in force for this run
written_utc    str    ISO-8601 UTC, when this row was appended
clip_path      str    saved clip for this event, project-relative; EMPTY on an
                      "open" row, when clips are disabled, or when none was
                      written. Records where the clip WAS put -- retention may
                      have deleted it since, so check before opening (§9.6)
```

See §9 for the write and read semantics. The two time bases are not redundant: `start_sec`/`end_sec` are what Phase 4 evaluation scores against ground truth, and `start_utc`/`end_utc` are what make a live-session event locatable in real time.

---

## 5. Phase 1 — Chunk encoding with cache

**This is the foundation. Get it right; everything reuses it.**

1. Read the video, resample to a fixed working FPS (default 8).
2. Split into **non-overlapping** chunks of `chunk_sec` (default 1.0 s). Each frame is encoded exactly once.
3. Sample each chunk to the model's expected frame count, run the encoder, mean-pool over tokens to one vector, L2-normalize.
4. Write to the cache. Skip work if the cache exists and `model_id` matches.

**Do not implement overlapping sliding windows here.** Overlap is created later by pooling adjacent cached chunk features. Encoding overlapping windows re-runs the encoder over every frame multiple times and makes threshold tuning unbearably slow.

**Acceptance:** encoding a 10-minute video twice — the second run reads from cache and completes in under a second.

---

## 6. Phase 2 — Prototype bank

For each class directory in `data/references/`:

1. Load the reference clip.
2. Generate 10–15 augmented variants:
   - temporal crop with jittered start (keep ≥70% of original duration)
   - speed resample at 0.8×, 1.0×, 1.25×
   - horizontal flip (**config flag per class**; disable when the action is chirally meaningful)
   - spatial crop jitter, 90–100% of frame
   - optional: light JPEG/blur degradation to narrow a reference-vs-target quality gap
3. Encode each variant the same way a chunk is encoded (same frame count, same pooling, same normalization). **The reference and target encoding paths must be identical** — any mismatch silently destroys similarity.
4. Save all variant vectors under the class name.

**Reference clip duration has a hard floor.** Everything is resampled to a fixed `frames_per_clip` before encoding, so `frames_per_clip / working_fps` seconds is a boundary: clips shorter than it are frame-REPEATED to fill the grid, clips at or above it are decimated to fit. A reference below the floor is padded with repeats while every target chunk is not, and similarity degrades for reasons unrelated to the action. `build_prototypes.py` warns when a reference falls short.

This is separate from the upper bound in §7.1 (a reference sets `W_base`, so it must not exceed the shortest action of interest). Together they bracket it: **at least `frames_per_clip / working_fps` seconds, at most the shortest instance you intend to catch.**

**Acceptance:** a reference clip scored against its own prototype bank returns similarity near 1.0. If not, the two encoding paths have diverged.

---

## 7. Phase 3 — Scoring and grouping

### 7.1 Window pooling
Build windows by mean-pooling **cached adjacent chunk features**, then re-normalizing. Window length `W` chunks, stride `S` chunks (default `S = max(1, W//4)`).

Run multiple `W` values (default `[0.7, 1.0, 1.4] × W_base`, rounded to chunks). Set `W_base` from the median reference clip duration.

> **Window length must be ≤ the shortest action of interest.** A window longer than the action mixes in background and the score drops. A window shorter than the action still resembles it. Err small.

### 7.2 Similarity
For each window and class: cosine similarity against every prototype variant, then take the **mean of the top 3**. More robust than mean-of-all (which is dragged down by off-distribution augmentations) and than max (which is noisy).

Take the max over window scales, per class, per time position.

### 7.3 Grouping into detections
Per class, over the time-indexed score series:

1. **Smooth** — moving average over 3 windows.
2. **Hysteresis** — open a detection when score > `tau_high`; keep it open while score > `tau_low` (default `0.85 × tau_high`); close below that.
3. **Minimum duration** — discard detections shorter than `0.5 × W_base`.
4. **Temporal NMS** — merge same-class detections overlapping by tIoU > 0.5, keeping the higher score.
5. **Cross-class resolution** — where classes overlap in time, keep the highest-scoring class.
6. **Reject** — any window whose max score across all classes is below `tau_high` is background. With 2–5 classes a single global threshold is sufficient.

This is dense scoring followed by contiguous-region grouping, the same structure as SSN / BSN / BMN proposal generation. A window never needs to align with the action boundary; a run of overlapping high-scoring windows recovers the full extent.

**Acceptance:** run on a video containing one known action instance; the detection covers it with tIoU ≥ 0.5.

---

## 8. Phase 4 — Evaluation and calibration

**Do not skip this phase. Do not let the user pick a threshold by eye.**

1. Require a hand-labeled eval set (see the labels contract). Target ≥20 total instances across classes and ≥30 minutes of video that includes plenty of background.
2. Sweep `tau_high` across the observed score range. At each value compute:
   - **Event-level precision and recall** at tIoU 0.5 (a detection is a true positive if it overlaps a same-class ground-truth event with tIoU ≥ 0.5; each ground-truth event matches at most one detection)
   - **False alarms per hour** = false positives ÷ (total video hours)
   - segment mAP at tIoU {0.3, 0.5, 0.7}
3. Emit a CSV of the sweep and print a per-class confusion matrix at the selected threshold.
4. Select `tau_high` from the false-alarms-per-hour budget in `config.yaml`.

**Never report frame-level accuracy or AUC.** On rare events these look excellent and mean nothing — a system can hit high AUC while producing tens of thousands of false alarms per hour.

**Acceptance:** `scripts/calibrate.py` outputs the sweep CSV, the chosen threshold, and its precision / recall / FA-per-hour.

---

## 9. Event log — persisting start and end timestamps

**Cross-cutting, not a phase.** Required from Phase 3 onward: as soon as grouping produces detections, they get written here. Both `detect.py` and `detect_live.py` write to the same file through the same `src/event_log.py` writer.

### 9.1 Write semantics

**Append-only. Never rewrite or edit a row in place.** A CSV cannot be updated in the middle safely while another process is tailing it, and a partial rewrite loses the whole file on a crash.

- **Offline (`source_type: video`)** — grouping produces complete events with both boundaries known. Write exactly one row per detection, `status: closed`. Never write `open` rows in offline mode.
- **Live (`source_type: live`)** — an event's end is not known when it starts. On hysteresis open, append a row with `status: open`, `start_sec`/`start_utc` filled, and `end_sec`/`end_utc`/`duration_sec` empty. On close, append a **second row with the same `event_id`**, `status: closed`, all fields filled. Do not go back and modify the open row.
- **Flush and `fsync` after every row** when `flush_each_event` is set. A live session may run for hours; a crash must not cost the events already detected.
- **One writer per file.** If concurrent runs are ever needed, give each its own file — do not attempt row-level locking.
- Write the header only when creating the file. Appending to an existing file must not repeat it.

### 9.2 Read semantics

Group rows by `event_id` and **take the last row for each**. That is the event's final state.

An `event_id` whose last row is `status: open` means the run ended — cleanly or by crash — with that detection still open. Such an event has a real start and no end. Consumers must handle this explicitly rather than dropping it or treating `end_sec` as zero.

### 9.3 Wall-clock derivation

Each run stamps a single `source_start_utc`, and every event's absolute times are derived from it: `start_utc = source_start_utc + start_sec`.

- **Live** — `source_start_utc` is the moment capture begins. Always known.
- **Video file** — taken from `--source-start-utc` if passed, else the file's mtime if `config.event_log.infer_video_start_from_mtime` is set, else **unknown**.

When it is unknown, leave `start_utc` and `end_utc` **empty**. Do not substitute the processing time and do not write a zero epoch — a fabricated wall-clock reading is worse than a missing one, because nothing downstream can tell it is fake.

### 9.4 Force-close

A detection held open past `max_open_sec` is closed with `end_sec` at that limit and the row flagged by setting `score` to the score at the moment of forcing. This is a guard against a stuck-high score pinning one event open for an entire session; if it fires often, the thresholds are wrong and Phase 4 needs re-running, not a larger limit.

### 9.5 Relationship to `dets.json`

`dets.json` remains the per-invocation output of `detect.py` and stays exactly as specified in §4 — Phase 4 evaluation reads that, not the CSV. The event log is the durable cross-run record. The two must agree for any offline run; a mismatch means a bug in the writer.

### 9.6 Saved clips

Optional, off unless `clips.enabled` is set. **This is the only part of the
system that writes recorded video to disk** — everything else holds embeddings
and timestamps. What accumulates in `clips.dir` is footage of the monitored
person, kept until the size budget evicts it or someone deletes it. Turning it on
is a deliberate decision, not a default.

- **Offline** — one sequential pass over the source file writes every detected
  span at the source's **native fps and resolution**. One pass, not one per
  detection: re-opening and seeking a long video per event is far slower.
- **Live** — cut from the retention buffer (§10.5) at `working_fps`, because
  those are the only frames that were kept. Live clips are therefore choppier
  than offline ones; the frames in between never existed on this path.

Both pad the detected span by `pre_roll_sec` / `post_roll_sec` and truncate at
`max_clip_sec`. A clip is written only when an event **closes** — its extent is
not known before that — so an event still open when a run ends has no clip.

**Retention.** After each run, clips are deleted oldest-first until the directory
is under `max_total_gb`. Rows in the event log are append-only and are never
rewritten, so `clip_path` can outlive its file. That is the accepted trade: a
dangling path is recoverable information, whereas rewriting log rows to keep them
in sync would break the append-only guarantee everything else depends on.

**Acceptance:** run `detect.py` on a video twice and `detect_live.py` for one session containing a known action. The CSV holds every detection from all three runs, each with a distinct `run_id`; the live event has an `open` row followed by a `closed` row sharing one `event_id`; the offline events have only `closed` rows; and the live event's `start_utc` matches the wall-clock moment the action actually happened, within the lag stated in §10.3.

---

## 10. Live capture mode

Camera feed in, same detections out. Build this **after Phase 3 grouping works offline and Phase 4 has produced a threshold** — live detection is meaningless without a calibrated `tau_high`, and `tau_high` can only be swept over cached offline features.

### 10.1 Capture and encoding

OpenCV `VideoCapture` on a background thread; drop frames rather than block if the encoder falls behind, and log the drop rate. Accumulate frames until one `chunk_sec` of working-FPS frames is buffered, then encode that chunk **exactly as an offline chunk is encoded** — same frame count, resize, normalization, pooling, L2 normalization. The §14.1 pitfall applies with full force here: three encoding paths (reference, offline target, live target) must now stay identical, not two.

Keep the last `max(window_scales) × W_base` chunk features in an in-memory ring buffer. **Live mode does not write to `cache/features/`** — an open-ended session would grow it without bound.

### 10.2 Online grouping

The Phase 3 grouping logic runs incrementally over the ring buffer: pool adjacent chunks into windows, score against the prototype bank, smooth, apply the same hysteresis with the same `tau_high` / `tau_low`. The state machine is the offline one with the future removed — it can open and close detections but cannot revise a decision after the fact.

Two Phase 3 steps cannot run online and are therefore **skipped in live mode**: temporal NMS (needs both events complete) and cross-class resolution (needs the full overlap picture). Consequence: live mode can emit two overlapping same-class detections where offline would have merged them, and can emit two classes over the same span. Accept this; do not fake it with a lookahead buffer. If a merged view is needed, post-process the CSV offline.

Minimum-duration filtering still applies, but only at close — an event shorter than `0.5 × W_base` is closed and its `open` row is left as the only trace. Note this in the reader.

### 10.3 Lag

A detection cannot be reported until the window covering it is complete and smoothed, so reporting lags the action by roughly `W_base/2 + smoothing_windows × stride` seconds. The **timestamps themselves are not lagged** — only the moment the row appears is late. State the measured lag in the run's startup log so it is never confused with a timestamp error.

Keeping that true takes two corrections, both for the same failure mode — a timestamp that says the action happened later than it did:

1. **Capture time, not a chunk counter.** A counter assumes chunks are produced at real-time cadence; on hardware that cannot encode that fast, wall time outruns it and the error accumulates without bound. Timestamp each chunk from when its frames were actually captured.
2. **Compensate the smoothing group delay.** Offline smoothing is centered; online it can only look backwards, and a trailing mean of `N` is the centered mean delayed by `(N-1)//2` samples. Uncompensated, every live event lands that much late — a full chunk at the default `smoothing_windows: 3`. The delay is deterministic, so attribute each smoothed score to the span it is centered on rather than to the newest chunk.

Neither is optional. A system whose output is timestamps cannot ship a known constant offset in them.

### 10.4 Sessions

Each `detect_live.py` invocation is one session: `source_id` = `live_<YYYYMMDD>T<HHMMSS>Z`, `source_type: live`, `start_sec` measured from capture start. On SIGINT, close any open detection normally (a real `closed` row, not a force-close) and flush before exiting.

### 10.5 Frame retention for clips

Needed only when `clips.enabled` is set; with clips off, the live path retains no video at all.

A live detection is recognised seconds after it began (§10.3), so by the time anything knows to keep the frames that make up its opening, those frames are already gone. Clip saving therefore requires a rolling buffer of recent frames held **before any detection exists**.

Retain the minimum that makes a clip possible:

- normally only the detection lag plus `pre_roll_sec`;
- while an event is open, nothing newer than its start minus pre-roll is evicted;
- a hard ceiling regardless, so one detection stuck open cannot grow the buffer without end.

Frames are JPEG-compressed on arrival. Raw 640×480 RGB is ~920 KB per frame, so a 15-second window at 8 fps would be ~110 MB held continuously; compressed it is a few MB. On a memory-constrained machine that difference decides whether live capture runs at all.

Post-roll comes free: closing already lags the true end by roughly the reporting lag, so frames past `end_sec` are usually in the buffer when the clip is cut. Write whatever is there — do not stall the loop waiting for post-roll.

**Acceptance:** perform a known action in front of the camera; the CSV gains an `open` row within the stated lag and a matching `closed` row when the action stops, and `start_utc` corresponds to when the action actually began. With clips enabled, the closed row's `clip_path` points at a playable file whose first frames precede the action.

---

## 11. Phase 5 — Only if Phase 4 falls short

Implement in this order. Re-run Phase 4 after each addition and keep it only if metrics improve.

### 11.1 Periodicity gate (cheapest, best payoff for repetitive actions)
Run RepNet (or a PyTorch port) to get a per-frame periodicity score. Multiply the similarity score for classes flagged `repetitive: true` in config. Helps most for hair-twirling; leave other classes untouched.

### 11.2 Trained linear head
Once ≥20 labeled instances per weak class exist, fit logistic regression on the frozen chunk features (positives from labeled events, negatives sampled from background). Replaces prototype similarity for that class. Expect a large gain — this is the highest-value step once data exists.

### 11.3 Pose branch (cascade, not parallel)
Run pose **only on candidate spans already proposed by the V-JEPA branch.**

1. RTMPose or MediaPipe → per-frame keypoints.
2. Normalize: center on hip, scale by shoulder width, remove global translation.
3. `sim_pose = exp(-soft_DTW(candidate, reference_skeleton) / temperature)`.
4. Fuse **at score level, per class**:

   `score_c = alpha_c * sim_vjepa + (1 - alpha_c) * sim_pose`

   `alpha_c` is per class in config. Low for geometrically-defined actions (hair-twirl), 1.0 for actions with no useful skeleton.

**Never concatenate pose and V-JEPA feature vectors.** With one example per class the relative scaling cannot be learned, and one modality silently dominates. Late fusion of scalar scores is the approach validated by PoseConv3D (Duan et al., CVPR 2022), where late RGB+pose fusion beats either stream alone and the pose stream is notably more robust to appearance and viewpoint shift.

### 11.4 Trained localizer (last resort)
The cached chunk features are already in the format TAL models consume (a per-chunk feature sequence). ActionFormer can be dropped in on the same cache to replace the hand-tuned grouping, given tens to low hundreds of labeled instances. Exemplar-conditioned methods (QAT, FMI-TAL) require meta-training on a base-class dataset first and are out of reach without one.

---

## 12. Config

```yaml
model_id: facebook/vjepa2-vitl-fpc64-256
working_fps: 8
chunk_sec: 1.0
window_scales: [0.7, 1.0, 1.4]
stride_ratio: 0.25
topk_prototypes: 3
smoothing_windows: 3
tau_high: null            # set by calibrate.py, never hand-edited
tau_low_ratio: 0.85
min_duration_ratio: 0.5
nms_tiou: 0.5
max_false_alarms_per_hour: 5

event_log:
  path: data/events/events.csv
  flush_each_event: true            # fsync after every row; a long session must survive a crash
  max_open_sec: 300                 # force-close a detection stuck open (§9.4)
  infer_video_start_from_mtime: false   # else offline start_utc/end_utc stay empty (§9.3)

clips:
  enabled: false                    # opt-in: the only thing that writes video to disk
  dir: data/clips
  pre_roll_sec: 2.0
  post_roll_sec: 2.0
  max_clip_sec: 60                  # longer events are truncated
  max_total_gb: 5.0                 # oldest clips deleted past this (§9.6)
  jpeg_quality: 80                  # live retention buffer only (§10.5)

live:
  camera_index: 0
  capture_fps: 30                   # device rate; frames are resampled to working_fps
  drop_on_backpressure: true        # drop frames rather than block the encoder; log the rate

classes:
  hair_twirl:
    allow_flip: true
    repetitive: true
    alpha_vjepa: 0.4
```

---

## 13. CLI

```
python scripts/build_prototypes.py --config config.yaml
python scripts/encode_video.py     --config config.yaml --video data/videos/x.mp4
python scripts/detect.py           --config config.yaml --video data/videos/x.mp4 --out dets.json \
                                   [--source-start-utc 2026-09-01T14:03:00Z] [--no-clips]
python scripts/detect_live.py      --config config.yaml [--camera 0]
python scripts/calibrate.py        --config config.yaml --labels data/labels/
```

All scripts read the same `config.yaml`. `detect.py` calls `encode_video.py` logic automatically if the cache is missing.

Both `detect.py` and `detect_live.py` append to `config.event_log.path` unconditionally — writing the log is not optional and has no disable flag. `--source-start-utc` supplies the wall-clock origin for a recorded file (§9.3); `detect_live.py` stamps its own and runs until SIGINT.

---

## 14. Pitfalls — check these before reporting success

1. **Reference and target encoding paths must be byte-identical** (frame count, resize, normalization, pooling). This is the single most common silent failure.
2. **L2-normalize everything** before cosine similarity, including after mean-pooling adjacent chunks.
3. **Never encode overlapping windows.** Pool cached chunks instead.
4. **Never report frame-level metrics.** Event-level plus false alarms per hour only.
5. **Never hand-pick a threshold.** It comes from the Phase 4 sweep.
6. **Cache invalidation** — key the cache on `model_id`, `working_fps`, and `chunk_sec`. Stale features produce plausible-looking garbage.
7. **Disable horizontal flip** for any class where mirroring changes the action's meaning.
8. **Log the score distribution** on background regions. If background scores sit close to action scores, the encoder is not discriminating and no threshold will save it — that is the signal to move to Phase 5.
9. **Three encoding paths now, not two** — reference, offline target, and live target. Live capture is the easiest one to let drift, because its frames arrive from OpenCV in BGR at the device's own rate rather than from decord. Assert on shape, dtype, and channel order at the encoder boundary.
10. **Never edit a row in the event log.** Append a second row with the same `event_id` and let the reader take the last one (§9.1, §9.2).
11. **Never fabricate a wall-clock timestamp.** If `source_start_utc` is unknown, `start_utc` and `end_utc` are empty. A plausible-looking wrong datetime is undetectable downstream.
12. **Never let `start_utc` reach the evaluator.** Phase 4 scores `start_sec`/`end_sec` against relative-second ground truth; mixing the two time bases produces silent nonsense.
13. **Reporting lag is not timestamp error** (§10.3). A live event appears in the CSV seconds after it began, but its recorded `start_sec` is the true onset. Do not "correct" for the lag.
14. **Live mode skips NMS and cross-class resolution** (§10.2), so its rows can overlap where offline rows would not. Do not compare live and offline event counts and conclude something is broken.
15. **A `clip_path` is not a promise the file exists.** Retention deletes oldest-first and log rows are never rewritten, so always check before opening (§9.6).
16. **Clips are the one thing here that writes video of the monitored person to disk.** Enabling them reverses the privacy posture of the rest of the system. Decide it deliberately, set a retention budget you have actually thought about, and know where `clips.dir` lives before pointing anything at a shared or synced folder.

---

## 15. Expected results

Well-separated, visually distinct actions should work acceptably from prototypes alone. Fine-grained self-directed behaviors (hair-twirling) will be the weakest class and will likely need Phase 5.1 and 5.2. Published 1-shot temporal localization ranges from roughly 55–68 mAP@0.5 on long well-separated actions down to under 25 on short densely-instanced ones, so expect wide variance by class and budget for the labeling step in Phase 5.2.
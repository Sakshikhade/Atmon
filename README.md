# ATMON (atmos-proj)

Privacy-first autism activity monitoring for families and clinicians.

**Product loop**

1. Family records a take in the handheld app (video stays on-device in IndexedDB).
2. On stop, the clip is scored by the local **action_detection** service (X-CLIP few-shot prototypes on port **8010**).
3. Events sync to **Supabase**; the family verifies / flags what to share.
4. The clinician app reviews only what a family has granted.

Do **not** run `python -m src.server` for this flow. That edge-dashboard path is legacy and is not part of family/clinician detection.

---

## Architecture

```text
┌─────────────────┐     POST /api/detect/video      ┌──────────────────────┐
│  Family Vite    │ ──────────────────────────────► │  action_detection    │
│  :5173          │                                 │  FastAPI :8010       │
│                 │ ◄──────── events JSON ───────── │  X-CLIP + prototypes  │
└────────┬────────┘                                 └──────────────────────┘
         │ saveCapture / flag / share
         ▼
┌─────────────────┐     share_grants + events       ┌─────────────────┐
│  Supabase       │ ──────────────────────────────► │ Clinician Vite  │
│  Auth + DB      │                                 │ :5180           │
└─────────────────┘                                 └─────────────────┘
```

| Layer | Role |
| :--- | :--- |
| `apps/family` | Capture, local video, post-capture detect call, verify/flag/share |
| `action_detection/` | Few-shot X-CLIP scoring (`POST /api/detect/video`) |
| Supabase | Auth, sessions, events, share grants |
| `apps/clinician` | Review flagged/shared events (does **not** call `:8010`) |

---

## Ports

| Service | Port | Command |
| :--- | :--- | :--- |
| Action detection | `8010` | See [Quick start](#quick-start) |
| Family app | `5173` | `cd apps/family && npm run dev` |
| Clinician app | `5180` | `cd apps/clinician && npm run dev` |

---

## Prerequisites

- **Python 3.11+** for `action_detection`
- **Node.js 20+** for the Vite apps
- **Supabase** project (URL + anon key)
- Webcam (for recording)
- Local detector assets (gitignored — supply per machine):
  - `action_detection/cache/prototypes_xclip.npz` (prototype bank)
  - `action_detection/models/pose_landmarker.task` (ear-cover wrist gate)
  - Optional: `action_detection/models/edgeface_xs_gamma_06.pt` + BlazeFace (identity; **off** by default for family)

First detect may download **X-CLIP** (`microsoft/xclip-base-patch16`) from Hugging Face (~750 MB).

---

## Quick start

### 1. Action detection (terminal 1)

```bash
cd action_detection

# Use an existing venv if you have one (example: sibling checkout), or create local:
#   source ../../action_detection/.venv/bin/activate
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Ensure cache/prototypes_xclip.npz exists (rebuild via demo UI / scripts if needed)
PORT=8010 python webapp/server.py
# → http://127.0.0.1:8010
# → POST http://127.0.0.1:8010/api/detect/video
```

### 2. Family app (terminal 2)

```bash
cd apps/family
cp .env.example .env
# Set:
#   VITE_SUPABASE_URL=...
#   VITE_SUPABASE_ANON_KEY=...
#   VITE_DETECT_URL=http://127.0.0.1:8010
# Leave VITE_MEDIA_URL unset (on-device video only).

npm install
npm run dev
# → http://127.0.0.1:5173
```

Sign in → **Record now** → stop → wait for **Saving…** / scoring → review events → flag / share.

| Outcome | `detector_version` |
| :--- | :--- |
| Detect service OK | `xclip-prototypes-v1` |
| Detect down / timeout / error | `stub` (local stand-in + toast) |

### 3. Clinician app (terminal 3, optional)

```bash
cd apps/clinician
cp .env.example .env
# Same VITE_SUPABASE_* as family. No VITE_DETECT_URL.

npm install
npm run dev
# → http://127.0.0.1:5180
```

Clinician accounts only see children/sessions covered by an active **share grant**. Flagged clips appear in the review queue after the family shares them.

More detail: [doc/detection.md](doc/detection.md).

---

## Detected classes

| Detector / `ClassKey` | Label in UI |
| :--- | :--- |
| `ear_cover` | Covering ears |
| `hair_twirling` | Hair twirling |
| `head_nodding` | Head nodding |

Legacy stub labels (`flap`, `vocal`, `mand`, `away`, `floor`) may still appear on older rows or stub fallback.

Config: `action_detection/config.yaml`  
Family mapper: `apps/family/src/lib/detector.ts`

---

## Model & runtime notes

- **Backbone:** X-CLIP (`backbone: xclip` in `config.yaml`).
- **Bank:** `cache/prototypes_xclip.npz` — classes `ear_cover`, `hair_twirling`, `head_nodding` (512-d).
- **Identity:** `identity.enabled: false` for unsupervised family post-capture (no Active Subject gallery required).
- **Family thresholds:** `/api/detect/video` softens uncalibrated tau / per-class scales so short handheld takes are not silent (`soften_for_family_detect` in `action_detection/webapp/server.py`). Live demo thresholds stay stricter.
- **Stop UX:** family shows **Saving…** while scoring; detect call times out (~45s) then falls back to stub.

Python deps: `action_detection/requirements.txt` (torch, transformers, mediapipe, fastapi, …).

---

## Configuration

| File | Purpose |
| :--- | :--- |
| `apps/family/.env` | `VITE_SUPABASE_*`, `VITE_DETECT_URL` |
| `apps/clinician/.env` | `VITE_SUPABASE_*` |
| `action_detection/config.yaml` | Backbone, classes, gates, thresholds |
| `.env` (repo root) | Legacy edge-monitor / optional infra only — **not** required for family detect |

Optional `VITE_MEDIA_URL` (legacy cloud media host) is unused in the default on-device flow. Leave it unset.

---

## Project structure

| Path | Contents |
| :--- | :--- |
| `apps/family/` | Family capture & review (Vite/React) |
| `apps/clinician/` | Clinician workspace (Vite/React) |
| `action_detection/` | X-CLIP detector + FastAPI + demo UI (`:8010`) |
| `supabase/` | SQL migrations |
| `doc/` | Product and detection docs |
| `src/`, `static/`, `samples/` | Legacy edge monitor / ingest / dashboards — **not** used by the family↔detect↔clinician loop |

---

## Testing

```bash
cd apps/family
npx --yes tsx --test src/lib/detector.test.ts
```

Smoke-check detect (with services running):

```bash
curl -sS -F "file=@samples/sample-2.mp4;type=video/mp4" \
  http://127.0.0.1:8010/api/detect/video | head
# expect detector_version: xclip-prototypes-v1
```

---

## Troubleshooting

| Symptom | Likely cause |
| :--- | :--- |
| Stop button looks stuck | Scoring in progress — badge should read **Saving…**; first X-CLIP load is slow |
| Toast: detector offline / stub events | `:8010` down, CORS, or detect timeout |
| `events: []` but `xclip-prototypes-v1` | No matching behaviour in the clip, or bank/thresholds; quiet clips are normal |
| 503 prototype bank missing | Place or rebuild `action_detection/cache/prototypes_xclip.npz` |
| Clinician queue empty | No share grant / nothing flagged — clinician does not call the detector |

---

## Documentation

- [Post-capture detection](doc/detection.md) — wiring, labels, run steps
- [Product scope](doc/product-scope.md) — goals and privacy
- [action_detection/README.md](action_detection/README.md) — detector internals / demo UI

---

## License

See [LICENSE](LICENSE).

EdgeFace weights/architecture used by identity (when enabled) are **CC BY-NC-SA 4.0** (non-commercial). See `action_detection/README.md` and the Hugging Face model card before commercial use.

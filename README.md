# ATMON / AAMAS (atmos-proj)

Privacy-first autism activity monitoring: a **family capture app**, **clinician review app**, local **Python edge monitor**, and a **few-shot action detector** (X-CLIP prototypes) wired for post-capture scoring.

Primary product loop: family records on device → local `action_detection` scores the clip → events sync to Supabase → family verifies / flags → clinician reviews shared clips.

---

## Executive Summary

- **Family + clinician web apps** (`apps/family`, `apps/clinician`) — Vite/React, Supabase Auth, local IndexedDB video, optional Backblaze media.
- **Post-capture detection** — after `stopTake`, the family app POSTs the recording to the `action_detection` service (`POST /api/detect/video` on port **8010**). Classes: `ear_cover`, `hair_twirling`, `head_nodding`.
- **Local edge monitor** — optional OpenCV + MediaPipe pipeline (`src/main.py`, `src.ingest`) for live/offline skeleton heuristics or RF ML, with dashboard replay (`src.server` on **8000**).
- **Privacy** — family video stays on-device by default; detector service is loopback-only; EdgeFace identity (in `action_detection`) is a session gate with a non-commercial licence (CC BY-NC-SA 4.0).

---

## Ports

| Service | Port | Command |
| :--- | :--- | :--- |
| Atmos API / media / static dashboards | `8000` | `python -m src.server --port 8000` |
| Action detection (X-CLIP) | `8010` | `cd action_detection && python webapp/server.py` |
| Family Vite app | `5173` | `cd apps/family && npm run dev` |
| Clinician Vite app | `5180` | `cd apps/clinician && npm run dev` |
| Webhook mock (optional) | `5001` | `python mock_server.py` |

---

## Prerequisites

- Python 3.11+ recommended
- Node.js 20+ (family / clinician apps)
- Git (with submodule support)
- Supabase project (URL + anon key for the Vite apps; `DATABASE_URL` for the Python server if using Postgres)
- Optional: webcam; Backblaze credentials for cloud clip upload

---

## Clone

```bash
git clone https://github.com/eAgni-Technologies/atmos-proj.git
cd atmos-proj
git checkout new_implementations   # or your working branch
git submodule update --init --recursive
```

`action_detection/` is a submodule of [Sakshikhade/Atmon](https://github.com/Sakshikhade/Atmon).

---

## Quick Start — Family app + post-capture detection

This is the main product path.

### 1. Atmos Python env

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Fill DATABASE_URL / DIRECT_URL and Backblaze vars if you use cloud media
```

### 2. Action detection service (terminal 1)

```bash
cd action_detection
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
# Optional identity gate: place models/edgeface_xs_gamma_06.pt
# Prototype bank: cache/prototypes_xclip.npz (or rebuild via the demo UI)
python webapp/server.py
# listens on http://127.0.0.1:8010
```

First encode may download X-CLIP weights from Hugging Face (~750 MB).

### 3. Atmos API (terminal 2)

```bash
cd /path/to/atmos-proj
source .venv/bin/activate
python -m src.server --port 8000
# http://127.0.0.1:8000  (replay dashboard)
# http://127.0.0.1:8000/family  (static family shell, if present)
```

### 4. Family Vite app (terminal 3)

```bash
cd apps/family
cp .env.example .env
# Required:
#   VITE_SUPABASE_URL=...
#   VITE_SUPABASE_ANON_KEY=...
#   VITE_MEDIA_URL=http://localhost:8000
#   VITE_DETECT_URL=http://127.0.0.1:8010
npm install
npm run dev
# http://localhost:5173
```

Sign in with a Supabase user → record a take → wait for scoring → verify / flag events.

If the detect service is down, the app toasts and falls back to a local stub (`detector_version: stub`). Successful runs use `xclip-prototypes-v1`.

### 5. Clinician Vite app (optional, terminal 4)

```bash
cd apps/clinician
cp .env.example .env
# Same VITE_SUPABASE_* and VITE_MEDIA_URL=http://localhost:8000
npm install
npm run dev
# http://localhost:5180
```

Use a clinician account invited by a family share grant.

More detail: [doc/detection.md](doc/detection.md).

---

## Quick Start — Classic edge monitor (optional)

Live camera HUD and offline file ingest (MediaPipe skeleton + heuristics / RF). Independent of the family Vite apps.

```bash
source .venv/bin/activate
cp .env.example .env

# Optional webhook sink
python mock_server.py

# Live camera
python src/main.py

# Offline video → SQLite episodes
python -m src.ingest samples/sample-2.mp4

# Skeleton replay dashboard
python -m src.server --port 8000
# http://127.0.0.1:8000
```

---

## Key Features

- **Handheld family capture** with pre-roll, assent marks, privacy blur helpers, and offline outbox sync.
- **Post-capture X-CLIP detection** via local FastAPI (`action_detection`), mapped into family `ClassKey`s.
- **Clinician review** of flagged / shared sessions with clip playback and judgements.
- **Live edge monitoring** with HUD, webhook / optional SMS alerts, CSV + SQLite episode log.
- **Skeleton replay dashboard** (11-joint trajectories; no raw video in that path).

---

## Project Structure

| Path | Contents |
| :--- | :--- |
| `apps/family` | Family capture & review (Vite/React) |
| `apps/clinician` | Clinician workspace (Vite/React) |
| `action_detection/` | Git submodule — few-shot X-CLIP detector + demo UI; API on `:8010` |
| `src/` | Edge monitor, ingest CLI, event storage, media helpers, HTTP server |
| `static/` | Single-file HTML dashboards (replay / legacy family shell) |
| `supabase/` | SQL migrations for app schema |
| `tests/` | Python unit/integration tests |
| `samples/` | Sample videos for ingest |
| `scripts/` | Local helpers (e.g. `scripts/reset-local.sh`) |
| `data/` | Runtime artifacts (SQLite, CSV) — gitignored |
| `doc/` | Architecture, setup, detection wiring |
| `mock_server.py` | Local webhook receiver |
| `train_classifier.py` | Train RF stimming classifier (edge path) |

---

## Configuration

| File | Purpose |
| :--- | :--- |
| `.env` | Python server: camera, webhooks, Twilio, `DATABASE_URL`, Backblaze |
| `apps/family/.env` | `VITE_SUPABASE_*`, `VITE_MEDIA_URL`, `VITE_DETECT_URL` |
| `apps/clinician/.env` | `VITE_SUPABASE_*`, `VITE_MEDIA_URL` |
| `action_detection/config.yaml` | Backbone, classes, identity gate, thresholds |

Do not commit real secrets. Prefer `.env.example` templates.

---

## Testing & Linting

```bash
# Python
source .venv/bin/activate
pytest
ruff check .

# Family detector mapper smoke test
cd apps/family
npx --yes tsx --test src/lib/detector.test.ts
```

---

## Documentation

- [Post-capture detection](doc/detection.md) — submodule, ports, family ↔ X-CLIP flow
- [Development Guide](doc/development.md) — setup, modes, alerting
- [Architecture](doc/architecture.md) — pipeline and threading (edge monitor)
- [Demo Guide](doc/demo_guide.md) — live / ingest walkthrough
- [Product Scope](doc/product-scope.md) — goals and privacy principles
- [Functional FAQ](doc/faq.md) — detection mechanics
- [Test Plan](doc/test_plan.md) — verification matrix

---

## License

See [LICENSE](LICENSE).

Note: `action_detection` EdgeFace weights/architecture are **CC BY-NC-SA 4.0** (non-commercial). See the submodule README / Hugging Face model card before commercial deployment.

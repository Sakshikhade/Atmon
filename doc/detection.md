# Post-capture action detection

The family app scores each finished take with the local **action_detection**
service (few-shot X-CLIP prototypes), then continues the usual store → family
verify → clinician review flow.

The detector lives in this repo at [`action_detection/`](../action_detection)
(first-party tree — no external submodule).

## Labels

| Detector class   | Family / clinician `ClassKey` |
|------------------|-------------------------------|
| `ear_cover`      | `ear_cover`                   |
| `hair_twirling`  | `hair_twirling`               |
| `head_nodding`   | `head_nodding`                |

Older stub classes (`flap`, `vocal`, `mand`, `away`, `floor`) remain for
legacy rows and fallback.

## Run locally

Terminal 1 — detection service (port **8010**):

```bash
cd action_detection
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python webapp/server.py
# POST http://127.0.0.1:8010/api/detect/video
```

Terminal 2 — atmos API (media / family backend):

```bash
# from atmos-proj root
source .venv/bin/activate
python -m src.server --port 8000
```

Terminal 3 — family Vite app:

```bash
cd apps/family
# .env must include:
#   VITE_DETECT_URL=http://127.0.0.1:8010
#   VITE_MEDIA_URL=http://localhost:8000
npm run dev
```

Open http://localhost:5173 → record a take → wait for scoring → review events
→ flag for clinician → open http://localhost:5180.

If the detect service is down, the app toasts and falls back to `detectStub`
with `detector_version: stub`. Successful runs set `detector_version` to
`xclip-prototypes-v1`.

## Prerequisites (action_detection)

- Prototype bank present locally (`action_detection/cache/prototypes_xclip.npz` or rebuild via the demo UI / scripts; `cache/` is gitignored)
- EdgeFace / pose weights under `action_detection/models/` when you enable identity or the ear wrist gate (large files are gitignored; copy or download per machine)
- Identity gate defaults to **off** in `action_detection/config.yaml` so family post-capture scoring does not require an Active Subject gallery
- First request may download X-CLIP weights from Hugging Face (~750 MB)
- Demo `tau_high` (1.5σ) is used when calibration is uncalibrated

## Mapper smoke test

```bash
cd apps/family
npx --yes tsx --test src/lib/detector.test.ts
```

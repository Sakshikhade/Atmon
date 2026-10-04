# Post-capture action detection

The family app does **not** run live scoring while the parent is recording.
There is no frame pump to `/api/frame` or `/api/live/*` from the handheld UI —
no on-screen event marks, class chips, or detect hints during a take.

Flow: **record → stop → save video on device → `POST /api/detect/video` →
review events** on the processing / details screens. The recorder can **delete
their own session** from the session screen (Supabase RLS + local IndexedDB).

The detector lives in this repo at [`action_detection/`](../action_detection)
(first-party tree — no external submodule). Live/demo APIs in
`action_detection/webapp` remain for the detector demo UI only.

The product loop does **not** use `python -m src.server` (legacy edge dashboard).
Family scoring runs only through `action_detection` on port **8010**.

## Labels

| Detector class   | Family / clinician `ClassKey` |
|------------------|-------------------------------|
| `ear_cover`      | `ear_cover`                   |
| `hair_twirling`  | `hair_twirling`               |
| `head_nodding`   | `head_nodding`                |

Older stub classes (`flap`, `vocal`, `mand`, `away`, `floor`) remain for
legacy rows and fallback.

## Family scoring path (`POST /api/detect/video`)

Uncalibrated handheld takes use a softened pipeline in
`action_detection/webapp/server.py` (live demo thresholds stay stricter):

1. **`soften_for_family_detect`** — cap per-class `tau_scale` / `confirm_sec`,
   lower uncalibrated τ, loosen ear-cover wrist gate to
   `FAMILY_DETECT_WRIST_NEAR_EAR` (**0.45**).
2. **Sigma hysteresis** — standard grouping against softened τ.
3. **Raw cosine floor** — also open spans where max raw cosine ≥
   `FAMILY_RAW_MATCH_FLOOR` (**0.75**), so quiet clips that fail z-score still
   surface real ear-cover takes.
4. **`resolve_cross_class`** — merge sigma + floor spans so one gesture is not
   split into competing labels (e.g. ear vs hair).
5. **`refine_family_detections`** — wrist-confirmed `ear_cover` drops
   `hair_twirling`; a clear raw winner drops weaker side events (e.g.
   settle-in `head_nodding`).
6. **Wrist gate force-close** — when pose says wrists left the ears mid-span,
   the hysteresis tracker closes the open event so later ear-cover is a new
   span (avoids one glued event across a gap).

Identity stays **off** for family post-capture (`identity.enabled: false` in
`config.yaml`).

## Session delete

- UI: session details → **Delete this session** (confirm sheet).
- Client: `deleteSession` in `apps/family/src/lib/api.ts` clears `ml_jobs`,
  then hard-deletes `sessions` (`.select` so a no-op RLS miss is visible),
  with soft `processing_status: deleted` fallback; then drops local video.
- DB: `supabase/migrations/20261004190000_session_delete_recorder.sql` —
  recorder-owned DELETE policies on `sessions`, `events`, `session_segments`,
  `assent_events`, and `ml_jobs`.

Apply that migration on the Supabase project before expecting cloud delete to
succeed.

## Run locally

Terminal 1 — detection service (port **8010**):

```bash
cd action_detection
source .venv/bin/activate   # or: python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt   # first time
PORT=8010 python webapp/server.py
# POST http://127.0.0.1:8010/api/detect/video
```

Terminal 2 — family Vite app:

```bash
cd apps/family
# .env must include:
#   VITE_SUPABASE_URL=...
#   VITE_SUPABASE_ANON_KEY=...
#   VITE_DETECT_URL=http://127.0.0.1:8010
# Leave VITE_MEDIA_URL unset (on-device IndexedDB only).
npm run dev
```

Terminal 3 — clinician (optional):

```bash
cd apps/clinician
# Same VITE_SUPABASE_*; no detect URL required
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
- Family constants live next to `soften_for_family_detect` in `webapp/server.py`
  (`FAMILY_RAW_MATCH_FLOOR`, `FAMILY_DETECT_WRIST_NEAR_EAR`,
  `FAMILY_DOMINANT_RAW_MARGIN`)

## Mapper smoke test

```bash
cd apps/family
npx --yes tsx --test src/lib/detector.test.ts
```

Wrist-gate force-close unit coverage:

```bash
cd action_detection
python -m pytest tests/test_scoring.py -q -k wrist_gate
```

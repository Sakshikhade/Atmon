# Developer Guide — Local ATMON Stack

> Navigation: [Docs Index](../README.md) / Runbooks / **Developer Guide**  
> Also: root [README.md](../../README.md) · `make help`

---

## Prerequisites

- Python 3.11+ (`action_detection`)
- Node.js 20+
- Supabase project (URL + anon key)
- Webcam for family capture
- Local detect assets: `action_detection/cache/prototypes_xclip.npz`, pose model under `models/`

---

## Three-terminal start

### Terminal 1 — Detection (:8010)

```bash
cd action_detection
python3 -m venv .venv && source .venv/bin/activate   # first time
pip install -r requirements.txt                      # first time
PORT=8010 python webapp/server.py
```

### Terminal 2 — Family (:5173)

```bash
cd apps/family
cp .env.example .env   # if needed
# VITE_SUPABASE_URL, VITE_SUPABASE_ANON_KEY, VITE_DETECT_URL=http://127.0.0.1:8010
npm install
npm run dev
```

### Terminal 3 — Clinician (:5180, optional)

```bash
cd apps/clinician
cp .env.example .env
# Same VITE_SUPABASE_*; no VITE_DETECT_URL
npm install
npm run dev
```

---

## Quality gates

```bash
make help
make validate-local    # detect tests + family smoke + app builds + docs audit
```

See [AGENTS.md](../../AGENTS.md) §3.

---

## Common pitfalls

| Symptom | Fix |
|---|---|
| Stub events after stop | Start `:8010`; check `VITE_DETECT_URL` |
| Empty events with real detector | See [detection.md](../detection.md) thresholds |
| Delete session fails | Apply migration `20261004190000_session_delete_recorder` |
| Wrong docs path | Use `docs/` (not `doc/`) |

---

## Remotes (optional)

| Remote | Repo |
|---|---|
| `origin` | eAgni-Technologies/atmos-proj |
| `atmon` | Sakshikhade/Atmon |

Push the same branch to both when the user requests dual sync.

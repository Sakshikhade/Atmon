---
name: ops-protocol
description: >-
  Use this skill for ATMON local ops: /validate-local, /validate-docs, /start-local
  guidance. Maps slash verbs to Makefile targets and the three-terminal product stack.
---

# Operational Lifecycle Protocol (`/validate-local` · `/validate-docs` · `/start-local`)

## Commands

| Command | Makefile / action | Purpose |
|---|---|---|
| **`/validate-local`** | `make validate-local` | All 4 gates: detect pytest, family mapper, app builds, docs audit |
| **`/validate-docs`** | `make validate-docs` | Markdown relative link audit (`scripts/audit_docs.py`) |
| **`/start-local`** | Manual (see below) | Start detect `:8010`, family `:5173`, clinician `:5180` |

## `/validate-local`

1. Run `make validate-local` from repo root.
2. Confirm each gate green:
   - `make test-detect`
   - `make test-family`
   - `make build-apps`
   - `make validate-docs`
3. Summarize results for the user before opening a PR.

## `/start-local`

There is no single process supervisor yet. Start three terminals per
[docs/runbooks/DEVELOPER_GUIDE.md](../../../docs/runbooks/DEVELOPER_GUIDE.md):

1. `cd action_detection && PORT=8010 python webapp/server.py`
2. `cd apps/family && npm run dev`
3. `cd apps/clinician && npm run dev` (optional)

Verify:

- `curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8010/docs` (or detect health)
- Family http://127.0.0.1:5173
- Clinician http://127.0.0.1:5180

## Notes

- Prefer `action_detection/.venv` for Python.
- Leave `VITE_MEDIA_URL` unset for on-device video.
- Do not start `python -m src.server` for the product demo.

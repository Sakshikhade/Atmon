# ATMON System Overview

> Navigation: [Docs Index](../README.md) / **Architecture**

Current product architecture for privacy-first family capture and clinician review.
Legacy edge-monitor architecture: [archive/legacy-aamas/architecture.md](../archive/legacy-aamas/architecture.md).

---

## Mental model

```text
Family Vite (:5173)
  record on-device (IndexedDB)
       │
       │  stop → POST /api/detect/video
       ▼
action_detection FastAPI (:8010)
  X-CLIP + prototypes (post-capture only)
       │
       │  events JSON → saveCapture
       ▼
Supabase (Auth + Postgres + RLS)
  sessions, events, share_grants, access_log
       │
       │  grant-scoped read
       ▼
Clinician Vite (:5180)
  review / verify / export (no :8010 call)
```

---

## Bounded contexts

| Context | Path | Responsibility |
|---|---|---|
| Family capture | `apps/family` | Record, local video, detect call, verify, flag, share, delete own session |
| Detection | `action_detection` | Few-shot X-CLIP scoring; demo Live UI separate from family |
| Platform data | `supabase/` | Auth, households, sessions, events, grants, RLS |
| Clinician review | `apps/clinician` | Grant-scoped queue; does not call detect |

---

## Invariants

1. **Post-capture only** for family — no `/api/frame` or `/api/live/*` from the handheld UI. See [ADR-001](decisions/ADR-001_POST_CAPTURE_FAMILY_DETECT.md).
2. **Video on-device by default** — IndexedDB; cloud media only if `VITE_MEDIA_URL` is set.
3. **Identity off** for family scoring (`identity.enabled: false` in `action_detection/config.yaml`).
4. **No scoring/ranking people** — logs behaviors neutrally; no grades or leaderboards.
5. **Family owns sharing** — clinicians see only granted clips/sessions; re-share is out of scope for v1.
6. **Do not use** `python -m src.server` for the product loop (legacy dashboard).

---

## Ports

| Service | Port |
|---|---|
| action_detection | 8010 |
| Family app | 5173 |
| Clinician app | 5180 |

---

## Related

- [detection.md](../detection.md) — scoring pipeline details
- [CAPABILITY_MATRIX.md](../product/CAPABILITY_MATRIX.md) — screen ↔ API ↔ table map
- [DEVELOPER_GUIDE.md](../runbooks/DEVELOPER_GUIDE.md) — local run

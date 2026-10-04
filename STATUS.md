# Project Status & Executive Summary

| Property | Value |
| :--- | :--- |
| **Project** | ATMON (atmos-proj) |
| **Current Health** | On Track — B2C core loop working |
| **Branch** | `new_implementations` |
| **Last Updated** | 2026-10-04 (Sprint 1 assigned) |

---

### 1. Elevator Pitch

Privacy-first autism activity monitoring: families record handheld takes on-device,
score behaviors post-capture with local X-CLIP detection, verify events, and share
scoped clips with clinicians — without live surveillance while recording.

### 2. Latest Deliveries

- Post-capture family detect (no liveDetect); soften/merge/refine scoring path.
- Session delete for recorder (RLS + IndexedDB).
- Docs/governance parity with nexus-pulse shape (`docs/`, `AGENTS.md`, sprints).

### 3. Current Focus & Next Milestone

- **Sprint 1** active — **Sakshi:** S1-T02 → S1-T01; **Mateo:** S1-T04 → S1-T03 → S1-T05.
- Tracker: [docs/sprints/SPRINT_TRACKER.md](docs/sprints/SPRINT_TRACKER.md)

### 4. Blockers & Risks

- **Blockers:** None for local core loop.
- **Risks:** Clinical export still depends on media-host path; vocal detection stub-only; compliance (COPPA/jurisdiction) not productized.

### 5. Verified Quality Metrics

- Run `make validate-local` before PRs (detect pytest, family mapper, app builds, docs audit).
- Gap scoring: [docs/sprints/ASSESSMENT.md](docs/sprints/ASSESSMENT.md)

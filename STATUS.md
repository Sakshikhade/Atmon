# Project Status & Executive Summary

| Property | Value |
| :--- | :--- |
| **Project** | ATMON (atmos-proj) |
| **Current Health** | On Track — B2C core loop working; Sprint 1 ACTIVE (W1 → 2026-10-10) |
| **Branch** | `new_implementations` |
| **Last Updated** | 2026-10-05 (capability matrix + 4-person replan) |

---

### 1. Elevator Pitch

Privacy-first autism activity monitoring: families record handheld takes on-device,
score behaviors post-capture with local X-CLIP detection, verify events, and share
scoped clips with clinicians — without live surveillance while recording.

### 2. Latest Deliveries

- Post-capture family detect (no liveDetect); soften/merge/refine scoring path.
- Session delete for recorder (RLS + IndexedDB).
- Docs/governance parity with nexus-pulse shape (`docs/`, `AGENTS.md`, sprints).
- F-ID capability spreadsheet + inventory audit gate (`docs/product/ATMON_Capability_Matrix.csv`) with detailed PASS/FAIL acceptance criteria and `QA` / `QA_Status` columns.

### 3. Current Focus & Next Milestone

- **Hard window:** 2026-10-06 → **2026-10-24** (pilot-blockers committed; S4/S5 best-effort).
- **Sprint 1 (W1):** **Sakshi** S1-T02 → S1-T01 → S1-T06; **Mateo** S1-T04 → S1-T03 → S1-T05; **Abhishek** S6-T05a staging smoke.
- Tracker: [docs/sprints/SPRINT_TRACKER.md](docs/sprints/SPRINT_TRACKER.md)
- Matrix: [docs/product/ATMON_Capability_Matrix.csv](docs/product/ATMON_Capability_Matrix.csv)

### 4. Team roles (four-person)

| Person | Role |
| :--- | :--- |
| **Mateo** | Major FE + BE (`apps/family`, `apps/clinician`, platform) |
| **Sakshi** | Sprint 1 FE (kept); major ML / detect thereafter |
| **Abhishek** | Testing / **QA** (matrix `QA` + `QA_Status`) **and hosting** (default Hosting_Owner) |
| **Mainak** | Review, merge, pilot sign-off; may host when Notes override |

### 5. Blockers & Risks

- **Blockers:** None for local core loop.
- **Risks:** Clinical export media-host path; vocal detection stub-only; COPPA/jurisdiction need design-partner geography (see TODOS.md); schedule fiction if W3 treats all S4/S5 as must-ship.

### 6. Verified Quality Metrics

- Run `make validate-local` before PRs (detect pytest, family mapper, app builds, docs audit, **capability matrix inventory**).
- Gap scoring: [docs/sprints/ASSESSMENT.md](docs/sprints/ASSESSMENT.md)

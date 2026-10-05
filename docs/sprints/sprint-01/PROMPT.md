# Sprint 1 PROMPT — Pilot blockers: data control & household admin

| Field | Value |
|---|---|
| Sprint | 01 |
| Status | ACTIVE |
| Wave / due | W1 · **2026-10-10** |
| Assignees | **Sakshi** (S1-T02 → S1-T01 → S1-T06), **Mateo** (S1-T04 → S1-T03 → S1-T05), **Abhishek** (S6-T05a staging smoke) |
| Hosting | Default **Abhishek** on cloud/shared-host work (Mainak Notes override only) |
| QA | Default **Abhishek**; update `QA_Status` in [ATMON_Capability_Matrix.csv](../../product/ATMON_Capability_Matrix.csv) when AC verified |
| Outcome | Design-partner family can create a household, manage who records, and fully export or delete their data; detect fixtures seeded; staging smoke green |
| Tracker | [SPRINT_TRACKER.md](../SPRINT_TRACKER.md) |
| Assessment | [ASSESSMENT.md](../ASSESSMENT.md) § Sprint 1 |
| Matrix | [ATMON_Capability_Matrix.csv](../../product/ATMON_Capability_Matrix.csv) |

---

## Tasks

### S1-T02 — Self-serve household + child creation — **Sakshi** (start here)

- **Backlog:** F-8.1  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 8  
- **Hosting:** Abhishek if cloud seed needed  
- **Acceptance:** New account can create household and first child without SQL seed.  
- **Hints:** Sign-in / splash flow in `apps/family`; RPC or inserts for `households`, `household_members`, `children`, `child_guardianships`

### S1-T01 — Household member invite — **Sakshi** (after T02)

- **Backlog:** F-5.1, F-7.1  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 5  
- **Hosting:** Abhishek  
- **Acceptance:** Admin can invite a member with role + `can_record`; invitee joins household.  
- **Hints:** `apps/family/src/FamilyApp.tsx` (“Add someone” sheet), `apps/family/src/lib/api.ts`, `household_members` RLS in `supabase/migrations/`

### S1-T04 — Family delete everything (verifiable) — **Mateo** (start here)

- **Backlog:** F-6.3  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 8  
- **Hosting:** Abhishek  
- **Acceptance:** Hard-delete household-owned rows; UI confirms rows gone; local IndexedDB cleared.  
- **Hints:** Extend beyond `deleteSession` (`api.ts`); may need broader DELETE RLS; clear `mediaStore`

### S1-T03 — Family export everything — **Mateo** (after T04)

- **Backlog:** F-6.3  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 5  
- **Hosting:** Abhishek  
- **Acceptance:** Export downloads structured metadata + event log; video policy documented (on-device vs cloud).  
- **Hints:** Replace toast stub in FamilyApp export action; document in `docs/detection.md` or runbook

### S1-T05 — Retention expiry job — **Mateo** (after T03)

- **Backlog:** F-6.1  
- **Track:** `BE-Platform` · **Pts:** 5  
- **Hosting:** Abhishek  
- **Acceptance:** Scheduled/job path respects `retention_policies`; flagged sessions promoted; unflagged age out.  
- **Hints:** `retention_policies` table; avoid relying only on legacy `src/family_store.py`

### S1-T06 — Detect fixture kickoff — **Sakshi** (after T01 or parallel)

- **Backlog:** F-2.2, F-2.5  
- **Track:** `BE-Detect` · **Pts:** 3  
- **Hosting:** Abhishek if shared detect box  
- **Acceptance:** Seed fixture videos / harness notes for ear-cover, hair-twirling, head-nodding empty vs mislabel cases (feeds S3-T02).  
- **Hints:** `action_detection` fixtures; keep family path post-capture only

### S6-T05a — Staging smoke (W1 parallel) — **Abhishek**

- **Backlog:** hosting / pilot path  
- **Track:** Hosting · **Pts:** 3  
- **Hosting:** Abhishek  
- **Acceptance:** Shared detect URL, family/clinician static hosts, and Supabase project are reachable for the team; secrets documented for shared env. Formal cert remains **S6-T05** in W3.  
- **Hints:** Runbook under `docs/runbooks/`; Hosting_Owner default Abhishek

---

## Quality gates before PR

```bash
make validate-local
```

Includes `make validate-capability-matrix`.

## Branch pattern

`feat/s1-t0N-<slug>` — one open PR per task (gatekeeper rule in [AGENTS.md](../../../AGENTS.md)).

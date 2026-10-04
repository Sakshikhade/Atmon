# Sprint 1 PROMPT — Pilot blockers: data control & household admin

| Field | Value |
|---|---|
| Sprint | 01 |
| Status | ACTIVE |
| Assignees | **Sakshi** (S1-T02 → S1-T01), **Mateo** (S1-T04 → S1-T03 → S1-T05) |
| Outcome | Design-partner family can create a household, manage who records, and fully export or delete their data |
| Tracker | [SPRINT_TRACKER.md](../SPRINT_TRACKER.md) |
| Assessment | [ASSESSMENT.md](../ASSESSMENT.md) § Sprint 1 |

---

## Tasks

### S1-T02 — Self-serve household + child creation — **Sakshi** (start here)

- **Backlog:** F-8.1  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 8  
- **Acceptance:** New account can create household and first child without SQL seed.  
- **Hints:** Sign-in / splash flow in `apps/family`; RPC or inserts for `households`, `household_members`, `children`, `child_guardianships`

### S1-T01 — Household member invite — **Sakshi** (after T02)

- **Backlog:** F-5.1, F-7.1  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 5  
- **Acceptance:** Admin can invite a member with role + `can_record`; invitee joins household.  
- **Hints:** `apps/family/src/FamilyApp.tsx` (“Add someone” sheet), `apps/family/src/lib/api.ts`, `household_members` RLS in `supabase/migrations/`

### S1-T04 — Family delete everything (verifiable) — **Mateo** (start here)

- **Backlog:** F-6.3  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 8  
- **Acceptance:** Hard-delete household-owned rows; UI confirms rows gone; local IndexedDB cleared.  
- **Hints:** Extend beyond `deleteSession` (`api.ts`); may need broader DELETE RLS; clear `mediaStore`

### S1-T03 — Family export everything — **Mateo** (after T04)

- **Backlog:** F-6.3  
- **Track:** `FE-Family` + `BE-Platform` · **Pts:** 5  
- **Acceptance:** Export downloads structured metadata + event log; video policy documented (on-device vs cloud).  
- **Hints:** Replace toast stub in FamilyApp export action; document in `docs/detection.md` or runbook

### S1-T05 — Retention expiry job — **Mateo** (after T03)

- **Backlog:** F-6.1  
- **Track:** `BE-Platform` · **Pts:** 5  
- **Acceptance:** Scheduled/job path respects `retention_policies`; flagged sessions promoted; unflagged age out.  
- **Hints:** `retention_policies` table; avoid relying only on legacy `src/family_store.py`

---

## Quality gates before PR

```bash
make validate-local
```

## Branch pattern

`feat/s1-t0N-<slug>` — one open PR per task (gatekeeper rule in [AGENTS.md](../../../AGENTS.md)).

---
name: sprint-lifecycle
description: >-
  ATMON sprint protocols: /sprint-start, /sprint-done, /sprint-update. Scaffolds
  envelopes under docs/sprints/, enforces gatekeeper + tri-registry with CHANGELOG.
---

# Sprint Lifecycle (`/sprint-start` · `/sprint-done` · `/sprint-update`)

Canonical process: [docs/runbooks/SPRINT_LIFECYCLE_PROCESS.md](../../../docs/runbooks/SPRINT_LIFECYCLE_PROCESS.md)  
Tracker: [docs/sprints/SPRINT_TRACKER.md](../../../docs/sprints/SPRINT_TRACKER.md)  
Agent contract: [AGENTS.md](../../../AGENTS.md)

## Pipeline

```text
/sprint-start → code + make validate-local → open PR → STOP (human QA)
→ user merges → /sprint-done (tri-registry) → /sprint-update (broadcast)
```

## `/sprint-start <id>`

Example: `/sprint-start 01`

1. Sync base (`dev` unless user says otherwise).
2. Confirm `docs/sprints/sprint-<id>/PROMPT.md` exists; create `REPORT.md` if missing.
3. Update registry row in `SPRINT_TRACKER.md` to ACTIVE.
4. For each task `S<id>-T<M>`:
   - Branch `feat/s<id>-t<M>-<slug>`
   - Implement + tests
   - Mark task IN PROGRESS → DONE in tracker when PR opened
   - `make validate-local`
   - `gh pr create` — **do not merge**
5. Halt for human local testing.

**Gatekeeper:** one task/PR at a time unless the user explicitly allows parallel work.

## `/sprint-done <id>`

After user merges all sprint PRs:

1. Complete `docs/sprints/sprint-<id>/REPORT.md` (PR numbers, gate evidence).
2. Sync tri-registry:
   - `CHANGELOG.md` — move `[Unreleased]` bullets into a dated section if releasing
   - `SPRINT_TRACKER.md` — DELIVERED
   - `BACKLOG.md` — epic progress notes
3. Do not force-push `main` / protected branches.

## `/sprint-update`

Write a short team message: shipped, next, blockers. No secrets or child PII.

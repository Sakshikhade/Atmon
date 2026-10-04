# Sprint Lifecycle Process

> Navigation: [Docs Index](../README.md) / [Sprint Tracker](../sprints/SPRINT_TRACKER.md)  
> Skills: `.agents/skills/sprint-lifecycle`, `pr-review-protocol`, `ops-protocol`

Five-verb pipeline (adapted from nexus-pulse):

```text
/sprint-start → implement + /validate-local → /review-pr (open PR)
       → human local test → user merges → /sprint-done → /sprint-update
```

---

## 1. `/sprint-start <id>`

1. Confirm base branch clean; pull latest.
2. Ensure `docs/sprints/sprint-<id>/PROMPT.md` (+ `REPORT.md`) exist.
3. Register / refresh row in `SPRINT_TRACKER.md`.
4. For each task `S<id>-T<M>`: branch `feat/s<id>-t<M>-<slug>`, implement, mark tracker IN PROGRESS.
5. Run `make validate-local`.
6. Open PR — **do not merge**. Stop for human QA.

## 2. Human local testing

- Family `:5173` + detect `:8010` (+ clinician if needed).
- Push fixes to the **same** open PR branch.

## 3. `/review-pr`

- Agent audits diff against [AGENTS.md](../../AGENTS.md) invariants and gates.
- User merges when satisfied.

## 4. `/sprint-done <id>`

After merge:

1. Fill `REPORT.md` (PR links, gate proof).
2. Sync **tri-registry**: `CHANGELOG.md`, `SPRINT_TRACKER.md`, `BACKLOG.md`.
3. Mark sprint DELIVERED.

## 5. `/sprint-update`

Short team broadcast: what shipped, what’s next, blockers.

---

## Defects outside sprint

Use [ISSUES.md](../ISSUES.md) for P0 hotfixes that should not wait for a full sprint envelope.

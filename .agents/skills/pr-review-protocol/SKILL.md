---
name: pr-review-protocol
description: >-
  ATMON PR review checklist (/review-pr). Verifies quality gates, AGENTS.md
  invariants, and docs/changelog updates before recommending merge.
---

# PR Review Protocol (`/review-pr`)

Use when the user asks to review a PR or after `/sprint-start` opens a PR.

## Checklist

### 1. Scope & task ID

- [ ] PR title/body references `S<N>-T<M>` when sprint-tracked
- [ ] Diff matches PROMPT acceptance (no drive-by refactors)

### 2. Invariants ([AGENTS.md](../../../AGENTS.md))

- [ ] Family app still **post-capture only** (no new `/api/frame` or liveDetect)
- [ ] No `python -m src.server` wired into product loop
- [ ] No scoring/ranking of people in UI copy
- [ ] Identity remains off for family detect unless explicitly scoped
- [ ] Destructive/export actions are not toast-only stubs

### 3. Quality gates

```bash
make validate-local
```

- [ ] Detect pytest green
- [ ] Family mapper smoke green
- [ ] Family + clinician builds green
- [ ] Docs audit green

### 4. Docs & changelog

- [ ] `CHANGELOG.md` `[Unreleased]` updated for user-visible changes
- [ ] Tracker status updated if sprint task
- [ ] `docs/` links still valid (`make validate-docs`)

### 5. Security / privacy skim

- [ ] No secrets committed (`.env`, keys)
- [ ] RLS considered for new tables/policies
- [ ] No bulk media access path introduced

## Output to user

- Verdict: **approve** / **request changes**
- Gate evidence (commands + pass/fail)
- Residual risks
- Remind: **user merges** — agent does not merge unless explicitly asked

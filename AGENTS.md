# AGENTS.md — AI Agent Steering & Operational Instructions

> **Authoritative Agent Specification for ATMON (atmos-proj)**  
> Operational contract, execution protocol, and non-negotiable product invariants
> for all AI coding agents in this repository.

---

## 1. Roles & Collaboration Contract

- **Product Owner & Reviewer (User):** Defines requirements, reviews PR diffs,
  conducts QA/acceptance, and holds exclusive authority to merge Pull Requests.
- **Lead Developer (AI Agent):** Implements code, writes tests, runs quality
  gates, opens GitHub PRs via `gh`, and awaits review.

### The Gatekeeper Rule (Zero Parallel Task Drift)

```
[Start Task] → [Feature Branch] → [Quality Gates] → [GitHub PR] → [STOP & AWAIT USER MERGE]
```

> **NEVER** start a new task or create a subsequent feature branch until the
> open PR for the current task has been reviewed and merged by the User.

---

## 2. Standard Task Execution Protocol

When assigned a task `S<N>-T<M>` from
[`docs/sprints/SPRINT_TRACKER.md`](docs/sprints/SPRINT_TRACKER.md):

1. **Sync base branch** (usually `dev` or `main` as directed):
   ```bash
   git checkout dev
   git pull origin dev
   ```
2. **Create isolated feature branch:**
   ```bash
   git checkout -b feat/s<N>-t<M>-<short-description>
   ```
3. **Update sprint tracker** — mark task `IN PROGRESS` in
   `docs/sprints/SPRINT_TRACKER.md`.
4. **Implement code & tests** — prefer TDD; keep family path post-capture only.
5. **Run the mandatory quality gates** — all must be green before PR.
6. **Record changelog entry** under `[Unreleased]` in `CHANGELOG.md`.
7. **Submit Pull Request** (do not merge):
   ```bash
   git push -u origin HEAD
   gh pr create --base dev --title "..." --body "..."
   ```
8. **Halt & request review** — present PR link, summary, and gate proof. Do not
   proceed to the next task until the user confirms merge.

Optional: when the user asks to sync both remotes, push the same branch to
`origin` (`eAgni-Technologies/atmos-proj`) and `atmon` (`Sakshikhade/Atmon`).

---

## 3. Mandatory Quality Gates

Every Pull Request must provide terminal evidence for these gates:

| Gate | Command | Passing criteria |
|---|---|---|
| **1. Detect unit suite** | `make test-detect` | `pytest` green in `action_detection` |
| **2. Family mapper smoke** | `make test-family` | `detector.test.ts` green |
| **3. Apps build** | `make build-apps` | Family + clinician `npm run build` succeed |
| **4. Docs link audit** | `make validate-docs` | `scripts/audit_docs.py` reports 0 broken links |
| **5. Capability matrix** | `make validate-capability-matrix` | F-ID inventory + Hosting_Owner enum OK |

Convenience: `make validate-local` runs gates 1–5.

---

## 4. Architectural Invariants (Non-Negotiable)

1. **Post-capture family detect only** — no `/api/frame` or `/api/live/*` from
   `apps/family`. See [ADR-001](docs/architecture/decisions/ADR-001_POST_CAPTURE_FAMILY_DETECT.md).
2. **Do not use** `python -m src.server` for the product loop (legacy edge
   dashboard under `src/`).
3. **Video on-device by default** — IndexedDB; leave `VITE_MEDIA_URL` unset unless
   cloud media is explicitly in scope.
4. **Identity off for family** — `identity.enabled: false` for unsupervised
   post-capture; do not require Active Subject gallery for family scoring.
5. **No scoring or ranking people** — neutral behavior taxonomy; no grades,
   leaderboards, or caregiver rankings.
6. **Family owns data & sharing** — clinicians see only grant-scoped rows; do not
   add re-share or bulk media dump paths.
7. **UI design** — read [`DESIGN.md`](DESIGN.md) before visual changes; live CSS
   tokens in the apps win when they disagree with markdown.

---

## 5. Tooling & Environment Conventions

| Verb | Command |
|---|---|
| Cheat sheet | `make help` |
| All gates | `make validate-local` (`/validate-local`) |
| Docs audit | `make validate-docs` (`/validate-docs`) |
| Capability matrix | `make validate-capability-matrix` |
| Detect tests | `make test-detect` |
| Family smoke | `make test-family` |
| App builds | `make build-apps` |

Local product stack (see [DEVELOPER_GUIDE](docs/runbooks/DEVELOPER_GUIDE.md)):

1. `PORT=8010 python webapp/server.py` in `action_detection`
2. `npm run dev` in `apps/family` → `:5173`
3. `npm run dev` in `apps/clinician` → `:5180`

Python for detect: prefer `action_detection/.venv`. Node 20+ for Vite apps.

---

## 6. Documentation Tri-Registry

On sprint closeout (`/sprint-done`), keep these three in sync:

1. [`CHANGELOG.md`](CHANGELOG.md) — `[Unreleased]` → dated release notes
2. [`docs/sprints/SPRINT_TRACKER.md`](docs/sprints/SPRINT_TRACKER.md) — task statuses
3. [`docs/sprints/BACKLOG.md`](docs/sprints/BACKLOG.md) — epic progress

Skills: [`.agents/skills/`](.agents/skills/) (`ops-protocol`, `sprint-lifecycle`,
`pr-review-protocol`).

---

## 7. Legacy code

`src/`, `static/`, and `docs/archive/legacy-aamas/` are historical. Do not extend
them unless the user explicitly requests legacy edge-monitor work.

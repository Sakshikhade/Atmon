<a id="top"></a>

# ATMON Documentation Index

> Navigation: [Root README](../README.md) / **Docs Index**  
> Quick links: [Agents](#1-ai-agent-steering--process) · [Product](#2-product--detection) · [Architecture](#3-architecture) · [Sprints](#4-sprints--backlog) · [Runbooks](#5-runbooks) · [Legacy archive](#6-legacy-aamas-archive)

Welcome to the **ATMON (atmos-proj)** engineering documentation. Active docs describe the family → detect → Supabase → clinician product loop. Legacy edge-monitor material lives under [archive/legacy-aamas/](archive/legacy-aamas/).

---

## Active documentation sections

### 1. AI agent steering & process

- [AGENTS.md](../AGENTS.md) — roles, gatekeeper rule, task protocol `S<N>-T<M>`, quality gates, product invariants
- [STATUS.md](../STATUS.md) — short executive snapshot
- [DESIGN.md](../DESIGN.md) — UI design SSOT (CSS wins)

### 2. Product & detection

- [detection.md](detection.md) — post-capture scoring path (`POST /api/detect/video`)
- [PRODUCT_SCOPE.md](product/PRODUCT_SCOPE.md) — vision, privacy, scope (ATMON banner + legacy body)
- [ATMON_Capability_Matrix.csv](product/ATMON_Capability_Matrix.csv) — **F-ID status spreadsheet** (capability → AC → owners → dates)
- [CAPABILITY_MATRIX.md](product/CAPABILITY_MATRIX.md) — product loop / integration map (UI ↔ APIs ↔ tables)

### 3. Architecture

- [SYSTEM_OVERVIEW.md](architecture/SYSTEM_OVERVIEW.md) — current ATMON mental model
- [Decisions (ADRs)](architecture/decisions/)
  - [ADR-001: Post-capture family detect](architecture/decisions/ADR-001_POST_CAPTURE_FAMILY_DETECT.md)

### 4. Sprints & backlog

- [ASSESSMENT.md](sprints/ASSESSMENT.md) — backlog gap analysis vs repo (source scoring)
- [SPRINT_TRACKER.md](sprints/SPRINT_TRACKER.md) — live sprint registry & Kanban
- [BACKLOG.md](sprints/BACKLOG.md) — prioritized epics and deferred work
- [ISSUES.md](ISSUES.md) — defect ledger
- Sprint envelopes: [sprint-01](sprints/sprint-01/) … [sprint-06](sprints/sprint-06/)

### 5. Runbooks

- [DEVELOPER_GUIDE.md](runbooks/DEVELOPER_GUIDE.md) — local three-terminal setup
- [SPRINT_LIFECYCLE_PROCESS.md](runbooks/SPRINT_LIFECYCLE_PROCESS.md) — sprint start → PR → done
- [DEMO_WALKTHROUGH.md](runbooks/DEMO_WALKTHROUGH.md) — family → clinician demo script

### 6. Legacy AAMAS archive

Frozen docs for the standalone `src/` edge monitor (not the Vite product loop):

- [architecture.md](archive/legacy-aamas/architecture.md)
- [development.md](archive/legacy-aamas/development.md)
- [demo_guide.md](archive/legacy-aamas/demo_guide.md)
- [test_plan.md](archive/legacy-aamas/test_plan.md)
- [faq.md](archive/legacy-aamas/faq.md)

### 7. Changelog

- [CHANGELOG.md](../CHANGELOG.md) — Keep a Changelog release history

---

## Footer map

| Need | Go to |
|---|---|
| Run locally | [DEVELOPER_GUIDE](runbooks/DEVELOPER_GUIDE.md) or root [README](../README.md) |
| Agent rules | [AGENTS.md](../AGENTS.md) |
| Assign sprint work | [SPRINT_TRACKER](sprints/SPRINT_TRACKER.md) |
| Detection details | [detection.md](detection.md) |
| Legacy monitor | [archive/legacy-aamas](archive/legacy-aamas/) |

[↑ Top](#top)

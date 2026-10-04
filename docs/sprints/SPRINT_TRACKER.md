# ATMON — Engineering Sprint Tracker & Delivery Ledger

> Navigation: [Docs Index](../README.md) / [Backlog](BACKLOG.md) / **Sprint Tracker**  
> Assessment: [ASSESSMENT.md](ASSESSMENT.md) · Agents: [AGENTS.md](../../AGENTS.md)

---

## Sprint ownership & delivery registry

| Sprint ID | Objective | Lead / Assignee | Status | Envelope |
|---|---|---|:---:|---|
| **Sprint 1** | Pilot blockers: household admin, export/delete, retention | Sakshi + Mateo | **ACTIVE** | [PROMPT](sprint-01/PROMPT.md) · [REPORT](sprint-01/REPORT.md) |
| **Sprint 2** | Trust, assent, clinical export, clip-unit share | *TBD* | QUEUED | [PROMPT](sprint-02/PROMPT.md) |
| **Sprint 3** | Detect quality loop + vocal spike | *TBD* | QUEUED | [PROMPT](sprint-03/PROMPT.md) |
| **Sprint 4** | Compliance & clinician identity | *TBD* | QUEUED | [PROMPT](sprint-04/PROMPT.md) |
| **Sprint 5** | Pilot instrumentation & resilience | *TBD* | QUEUED | [PROMPT](sprint-05/PROMPT.md) |
| **Sprint 6** | Pilot polish & freeze | *TBD* | QUEUED | [PROMPT](sprint-06/PROMPT.md) |

---

## Transactional sprint folder protocol

Each sprint lives at `docs/sprints/sprint-<nn>/`:

| File | Purpose |
|---|---|
| `PROMPT.md` | Task breakdown `S<N>-T<M>`, acceptance, file hints |
| `REPORT.md` | Delivery ledger after merge (gates, PR links) |

On closeout, sync **tri-registry**: `CHANGELOG.md` + this tracker + `BACKLOG.md`.

Branch naming: `feat/s<N>-t<M>-<slug>` (see [AGENTS.md](../../AGENTS.md)).

---

## Sprint 1 Kanban — Pilot blockers

| Task ID | Story | Track | Pts | Status | Assignee |
|---|---|---|---|---|---|
| **S1-T02** | Self-serve household + child creation | FE-Family + BE-Platform | 8 | TODO | Sakshi |
| **S1-T01** | Wire “Add someone” / invite household member | FE-Family + BE-Platform | 5 | TODO | Sakshi |
| **S1-T04** | Family delete everything (verifiable) | FE-Family + BE-Platform | 8 | TODO | Mateo |
| **S1-T03** | Family export everything (metadata + event log) | FE-Family + BE-Platform | 5 | TODO | Mateo |
| **S1-T05** | Retention expiry job (flagged keep / unflagged age out) | BE-Platform | 5 | TODO | Mateo |

**Work order:** Sakshi: S1-T02 → S1-T01. Mateo: S1-T04 → S1-T03 → S1-T05.

**Exit criteria:** No toast-only stubs for export/delete-everything; new family can onboard without SQL seed.

---

## Sprint 1 team split

| Seat | Person | Track | Sprint 1 focus |
|---|---|---|---|
| Dev A | **Sakshi** | FE-Family (+ BE-Platform) | S1-T02 → S1-T01 (onboarding + invite) |
| Dev B | **Mateo** | BE-Platform | S1-T04 → S1-T03 → S1-T05 (delete / export / retention) |
| Dev C | *unassigned* | BE-Detect | Sprint 3+ (regression, vocal) |

---

## Definition of done (per task)

- Acceptance in PROMPT met  
- Migration applied if schema changes  
- Docs updated if behavior/docs change  
- `make validate-local` green  
- No new toast-only stubs for destructive/export actions  
- Family path remains post-capture only

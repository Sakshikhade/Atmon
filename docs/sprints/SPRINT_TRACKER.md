# ATMON — Engineering Sprint Tracker & Delivery Ledger

> Navigation: [Docs Index](../README.md) / [Backlog](BACKLOG.md) / **Sprint Tracker**  
> Assessment: [ASSESSMENT.md](ASSESSMENT.md) · Agents: [AGENTS.md](../../AGENTS.md)  
> Capability spreadsheet: [ATMON_Capability_Matrix.csv](../product/ATMON_Capability_Matrix.csv)

---

## Wave calendar (compressed — due 2026-10-24)

| Wave | Calendar | Committed track | Planned_Delivery |
|---|---|---|---|
| **W1** | Oct 6–10 | **S1** Sakshi T02/T01 + Mateo T04/T03/T05 + Sakshi S1-T06; **Abhishek S6-T05a staging smoke** | 2026-10-10 |
| **W2** | Oct 13–17 | **S2** Mateo (trust/share/clinical export); **S3** Sakshi ML (T02 regression + T03 vocal go/no-go) | 2026-10-17 |
| **W3** | Oct 20–24 | Best-effort S3 FE / S4 / S5; **S6** pilot script + **S6-T05 formal hosting cert** + deferral memo | **2026-10-24** |

**Pilot-ready ≠ all S1–S6 Done.** Must-ship by Oct 24: S1 household/export/delete/retention; S2 trust/share/clinical export/clips; S3-T02 (+ S3-T03 memo); S6-T01/T04/T05 (+ W1 smoke). S4/S5 and S5-T06–T08 are best-effort → S6-T04 if slipping.

**Hosting_Owner default:** Abhishek (Mainak only via Notes override).  
**QA default:** Abhishek · **QA_Status:** Pass (Done) / Pending (Partial|Not started) / N/A (Deferred). See [ATMON_Capability_Matrix.csv](../product/ATMON_Capability_Matrix.csv).

---

## Sprint ownership & delivery registry

| Sprint ID | Objective | Lead / Assignee | Status | Envelope | Wave end |
|---|---|---|:---:|---|---|
| **Sprint 1** | Pilot blockers: household admin, export/delete, retention + detect fixtures | Sakshi + Mateo (+ Abhishek staging smoke) | **ACTIVE** | [PROMPT](sprint-01/PROMPT.md) · [REPORT](sprint-01/REPORT.md) | 2026-10-10 |
| **Sprint 2** | Trust, assent, clinical export, clip-unit share | **Mateo** | QUEUED | [PROMPT](sprint-02/PROMPT.md) | 2026-10-17 |
| **Sprint 3** | Detect quality loop + vocal go/no-go | **Sakshi** (ML) + **Mateo** (FE/DB wire) | QUEUED | [PROMPT](sprint-03/PROMPT.md) | ML 2026-10-17; FE 2026-10-24 |
| **Sprint 4** | Compliance & clinician identity (best-effort) | **Mateo** (+ Sakshi S4-T06) | QUEUED | [PROMPT](sprint-04/PROMPT.md) | 2026-10-24 |
| **Sprint 5** | Instrumentation & resilience (best-effort) | **Mateo** | QUEUED | [PROMPT](sprint-05/PROMPT.md) | 2026-10-24 |
| **Sprint 6** | Pilot polish & freeze | **Abhishek** (script/cert) + **Mainak** (blast-radius/memo) | QUEUED | [PROMPT](sprint-06/PROMPT.md) | **2026-10-24** |

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

## Sprint 1 Kanban — Pilot blockers (W1 · due 2026-10-10)

| Task ID | Story | Track | Pts | Status | Assignee | Hosting |
|---|---|---|---|---|---|---|
| **S1-T02** | Self-serve household + child creation | FE-Family + BE-Platform | 8 | TODO | Sakshi | Abhishek if cloud seed |
| **S1-T01** | Wire “Add someone” / invite household member | FE-Family + BE-Platform | 5 | TODO | Sakshi | Abhishek |
| **S1-T04** | Family delete everything (verifiable) | FE-Family + BE-Platform | 8 | TODO | Mateo | Abhishek |
| **S1-T03** | Family export everything (metadata + event log) | FE-Family + BE-Platform | 5 | TODO | Mateo | Abhishek |
| **S1-T05** | Retention expiry job (flagged keep / unflagged age out) | BE-Platform | 5 | TODO | Mateo | Abhishek |
| **S1-T06** | Detect fixture kickoff (ear/hair/nod pack seed) | BE-Detect | 3 | TODO | Sakshi | Abhishek if shared detect box |
| **S6-T05a** | Staging smoke (detect URL + Supabase + static hosts up) | Hosting | 3 | TODO | Abhishek | Abhishek |

**Work order:** Sakshi: S1-T02 → S1-T01 (then S1-T06). Mateo: S1-T04 → S1-T03 → S1-T05. Abhishek: S6-T05a in parallel.

**Exit criteria:** No toast-only stubs for export/delete-everything; new family can onboard without SQL seed; staging endpoints reachable for later waves.

---

## Sprint 5 additions (W3 best-effort · due 2026-10-24)

| Task ID | Story | Backlog | Assignee | Notes |
|---|---|---|---|---|
| **S5-T06** | Subject-tracking pilot posture | F-2.3 | Mateo | Identity-off + tap-box; hide EdgeFace family demo if reachable |
| **S5-T07** | Storage tiering honesty | F-6.2 | Mateo | On-device default copy; cloud optional |
| **S5-T08** | Biometric-law posture doc + UI | F-7.4 | Mateo (+ Mainak skim) | No gallery required on family path |

Slip unfinished T06–T08 into S6-T04 deferral memo — do not slip past Oct 24.

---

## Sprint 6 highlights (W3 · due 2026-10-24)

| Task ID | Story | Assignee |
|---|---|---|
| **S6-T01** | End-to-end pilot script on hosted stack | Abhishek |
| **S6-T02** | Bug bash / P0 fixes | Mateo + Sakshi fix; Abhishek triage |
| **S6-T03** | Breach blast-radius review | Mainak |
| **S6-T04** | Deferral memo (billing / home cameras / agency / slipped W3) | Mainak |
| **S6-T05** | Formal staging/hosting cert (completes S6-T05a) | Abhishek |

---

## Team split (four-person)

| Seat | Person | Role |
|---|---|---|
| Dev A | **Sakshi** | Keeps S1 FE (T02→T01); ML thereafter (S1-T06, S3 detect) |
| Dev B | **Mateo** | Major FE + BE for S1 delete/export/retention and later app work |
| Dev C | **Abhishek** | Testing / **QA** (matrix `QA` column) + hosting (default Hosting_Owner); S6-T05a / S6-T05 / S6-T01 |
| Oversight | **Mainak** | Review, merge, pilot sign-off; may override Hosting_Owner in Notes; S6-T03/T04 |

---

## Definition of done (per task)

- Acceptance in PROMPT met  
- Migration applied if schema changes  
- Docs updated if behavior/docs change  
- `make validate-local` green (includes `validate-capability-matrix`)  
- No new toast-only stubs for destructive/export actions  
- Family path remains post-capture only

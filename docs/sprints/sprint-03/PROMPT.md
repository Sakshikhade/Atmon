# Sprint 3 PROMPT — Detection quality loop

| Field | Value |
|---|---|
| Sprint | 03 |
| Status | QUEUED |
| Wave / due | ML **2026-10-17**; FE best-effort **2026-10-24** |
| Lead | **Sakshi** (ML) + **Mateo** (FE/DB) · **QA** Abhishek |
| Outcome | Appearance detection stays reliable; verification feeds improvement; vocal gap scoped |
| Detail | [ASSESSMENT.md](../ASSESSMENT.md) § Sprint 3 · [ATMON_Capability_Matrix.csv](../../product/ATMON_Capability_Matrix.csv) |

## Tasks (titles)

| ID | Story | Pts | Impl | Backlog / AC highlight |
|---|---|---|---|---|
| S3-T01 | Verification → labeled feedback store | 5 | Sakshi + Mateo | F-2.5 - durable rows: event_id, decision, corrected_class, actor, ts |
| S3-T02 | Offline regression pack (ear/hair/nod) | 5 | Sakshi | F-2.2 - fixtures for empty vs mislabel (feeds S1-T06) |
| S3-T03 | Vocal / mand detection spike | 8 | Sakshi | F-2.7 - go/no-go memo (must-ship); not silent stub confidence |
| S3-T04 | Battery / storage warnings on capture | 3 | Mateo | F-1.4 - IndexedDB quota caution/block (best-effort W3) |
| S3-T05 | Pattern view time buckets | 3 | Mateo | F-3.5 - week/month counts; no ranking (best-effort W3) |

**QA:** Abhishek sets matrix `QA_Status` against PASS/FAIL ACs.

Expand further when activated (`/sprint-start 03`).

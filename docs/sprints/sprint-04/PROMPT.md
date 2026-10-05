# Sprint 4 PROMPT — Compliance & clinician identity

| Field | Value |
|---|---|
| Sprint | 04 |
| Status | QUEUED |
| Wave / due | W3 best-effort · **2026-10-24** |
| Lead | **Mateo** (S4-T01…T05); **Sakshi** (S4-T06); **QA** Abhishek |
| Outcome | Minimum legal/product guardrails for a US design-partner pilot |
| Detail | See [ASSESSMENT.md](../ASSESSMENT.md) § Sprint 4 · Matrix [ATMON_Capability_Matrix.csv](../../product/ATMON_Capability_Matrix.csv) |

## Tasks (titles)

| ID | Story | Pts | Impl | Notes / AC highlight |
|---|---|---|---|---|
| S4-T01 | COPPA checklist + disclosures | 5 | Mateo | Checklist in `docs/`; disclosures before first record (F-7.5) |
| S4-T02 | Purpose-specific consent unbundling | 3 | Mateo | Four toggles with separate storage: `backup`→`cloud_backup`, `recordings`→`train_on_recordings`, `corrections`→`train_on_corrections`, `analytics`→`product_analytics` in `app.consents` (purpose tag + granted + created_at). None required to use app (F-7.3) |
| S4-T03 | Immutable audit log | 5 | Mateo | Append-only audit for share/view/export/delete (F-7.6) |
| S4-T04 | Clinician org profile UX | 3 | Mateo | Org name on clinician + family invite (F-4.5) |
| S4-T05 | Jurisdiction profile stub | 5 | Mateo | US default disclosures; cut first if slipping (F-5.4) |
| S4-T06 | Vocal v1 or explicit deferral | 8 | Sakshi | Ship first vocal class or backlog deferral (F-2.7) |

**QA:** Abhishek verifies PASS/FAIL against matrix `Acceptance_Criteria` and sets `QA_Status`.

Expand further when Sprint 4 is activated (`/sprint-start 04`).

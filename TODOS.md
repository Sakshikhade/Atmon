# ATMON — Follow-up TODOs

Captured from capability-matrix eng review (2026-10-05). Not blocking the CSV / tracker execute.

## 1. Sync ASSESSMENT + S2–S6 PROMPTs

- **What:** Fill Impl owners / wave dates into `docs/sprints/ASSESSMENT.md` §4–7 and expand `docs/sprints/sprint-02`…`06/PROMPT.md` with Backlog F-ID lines matching ASSESSMENT.
- **Why:** Minimal docs pass left PROMPTs thinner than the tracker; agents still open stubs.
- **Depends on:** Merge of capability-matrix / tracker execute.
- **Owner:** Eng (any) after Mainak merges the matrix PR.

## 2. Re-audit CSV Status against live apps

- **What:** Walk Partial/Done rows in `docs/product/ATMON_Capability_Matrix.csv` against `apps/family`, `apps/clinician`, and `action_detection`; refresh Status that drifted.
- **Why:** CSV Status was copied from ASSESSMENT without a code re-audit (`Source=ASSESSMENT`).
- **Depends on:** Preferably after Sprint 1 (and S2) land.
- **Owner:** Abhishek (testing) + Impl owners for disputed rows.

## 3. Lock design-partner geography

- **What:** Decide first design-partner jurisdiction / US state for COPPA disclosures and F-5.4 jurisdiction stub priority.
- **Why:** ASSESSMENT §9 still open; F-5.4 / F-7.5 dated in W3 without geography.
- **Depends on:** Partner conversations.
- **Owner:** **Mainak** (product).

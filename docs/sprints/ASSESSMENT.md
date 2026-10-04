# ATMON Backlog Assessment & Sprint Plan

**Sources:** Product backlog (Epic level + Initiative level), Draft 8 · August 2026  
**Codebase:** `apps/family`, `apps/clinician`, `action_detection`, `supabase/` · branch `new_implementations`  
**Date:** 2026-10-04  
**Purpose:** What is done, what remains, and how to slice remaining B2C work into sprints for developer assignment.

> Status is measured against the **current Vite product loop**, not the legacy `src/` + `static/family.html` prototypes.

---

## 1. Executive summary

| Initiative | Intent | Status |
|---|---|---|
| **I-1 B2C-MVP** (E-1…E-8) | Family record → detect → log → share → clinician | **Core loop done**; consent / retention / accounts / onboarding **partial**; billing / metrics **not started** |
| **I-1 Phase 2** (E-9) | Home cameras | **Not started** |
| **I-2 B2B-Phase3** (E-10…E-15) | Agency platform | **Not started** (needs detailing pass before build) |

**What works today:** a family can record a handheld take, score it post-capture (`POST /api/detect/video`), verify events, flag/share with a clinician, and delete their own session.

**What blocks a real pilot:** verifiable full export/delete, household admin, self-serve setup, clinical export without a half-wired media host, vocal detection, and compliance surfaces (COPPA / jurisdiction / trust page).

**Rough feature scorecard (53 B2C features in E-1…E-9):**

| Status | Count |
|---|---|
| Done | ~14 |
| Partial | ~24 |
| Not started | ~15 |
| B2B E-10…E-15 | ~28 — all not started |

---

## 2. Assessment by epic

### E-1 Mobile Capture — mostly done

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-1.1 | Session Capture & Subject Designation | **Done** | Pre-roll, segments, tap subject |
| F-1.2 | On-Device Processing & Bystander Protection | **Partial** | Preview blur; scoring is local-server post-capture, not on-phone ML |
| F-1.3 | Capture Governance & Family Controls | **Done** | Consent keys, roles, redaction, assent marks |
| F-1.4 | Offline, Battery & Storage Resilience | **Partial** | IndexedDB + outbox; no battery/quota UX |

### E-2 Behavior Perception — detect core done; breadth open

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-2.1 | Taxonomy & anti-pathologization | **Done** | Neutral labels, `behavior_classes` |
| F-2.2 | Post-Capture Detection Engine | **Done** | Family pipeline on `:8010` |
| F-2.3 | Non-Biometric Subject Tracking | **Partial** | Boxes + tap; EdgeFace demo only, identity off for family |
| F-2.4 | Family & Clinician Verification | **Done** | Confirm / correct / reject |
| F-2.5 | Accuracy, Validation & Bias Monitoring | **Partial** | Detector eval scripts; no product bias dashboard |
| F-2.6 | Multi-Subject Group Attribution | **Not started** | Agency-oriented |
| F-2.7 | Verbal & Vocal Detection | **Partial** | Taxonomy/stub only; X-CLIP is appearance classes |
| F-2.8 | Low Light / Dynamic Lighting | **Not started** | |

### E-3 Behavior Log & Video Index — mostly done

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-3.1 | Timestamped Behavior Log | **Done** | |
| F-3.2 | Deep-Link into Recording | **Done** | Seek with antecedent lead-in |
| F-3.3 | Clip Extraction & Sharing Units | **Partial** | Share by event/scope; not standalone clip files |
| F-3.4 | Family Annotation & Notes | **Done** | |
| F-3.5 | Trend & Pattern Views | **Partial** | Simple counts; not longitudinal analytics |

### E-4 Clinician Sharing — mostly done

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-4.1 | Family-Controlled Share Grants | **Done** | Scope, expiry, revoke, download/export flags |
| F-4.2 | Clinician Review Workspace | **Done** | Queue, player, flags, capture asks |
| F-4.3 | Clinical Export | **Partial** | PDF path needs `VITE_MEDIA_URL` / media host |
| F-4.4 | Share Transparency & Audit | **Done** | `access_log` tallies |
| F-4.5 | Clinician Cross-Family Identity | **Partial** | Email invite + grants; no org SSO |

### E-5 Consent, Assent & Bystander — scaffolding

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-5.1 | Parent/Guardian Authority | **Partial** | Household membership; “Add someone” not wired |
| F-5.2 | Child Assent & Dignity | **Partial** | Pause → `assent_events`; limited dignity UX |
| F-5.3 | Bystander Consent | **Partial** | Copy + blur; formal ack incomplete in Vite |
| F-5.4 | Jurisdiction-Aware Controls | **Not started** | |
| F-5.5 | Exposure / Sexualized Controls | **Partial** | Manual blur/remove; no automated detector |

### E-6 Retention, Storage & Data Control — partial

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-6.1 | Family-Controlled Retention | **Partial** | Prefs in DB; no enforced expiry job |
| F-6.2 | Storage Tiering & Cost | **Partial** | Device-first + optional cloud |
| F-6.3 | Family Export & Deletion | **Partial** | Per-session delete done; export/delete-everything stubs |
| F-6.4 | Breach Blast-Radius | **Partial** | Grant scoping; no key-isolation / backup purge proof |

### E-7 Accounts, Privacy & Compliance — foundation

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-7.1 | Household Accounts & Auth | **Partial** | Supabase auth + roles; weak member admin |
| F-7.9 | Child Records & Multi-Household | **Not started** | No dual-household / split-record |
| F-7.2 | Encryption & US Residency | **Not started** | Infra/policy not productized |
| F-7.3 | Consumer Health Data Compliance | **Partial** | Purpose consent keys only |
| F-7.4 | Biometric-Law Posture | **Partial** | Family identity off; demo EdgeFace remains |
| F-7.5 | COPPA & App Store | **Not started** | |
| F-7.6 | Immutable Audit Logging | **Partial** | Access/export logs; not compliance-grade |
| F-7.7 | No-Surveillance Guardrails | **Partial** | No scores/ranks; covert-mode ban informal |

### E-8 Onboarding, Trust & Monetization — early

| ID | Feature | Status | Notes |
|---|---|---|---|
| F-8.1 | Self-Serve Onboarding | **Partial** | Intro/sign-in; no household/child creation |
| F-8.2 | Subscription & Billing | **Not started** | Open decision in backlog |
| F-8.3 | Trust & Transparency Surface | **Partial** | In-app trust screen; no public trust page |
| F-8.4 | Design-Partner Instrumentation | **Not started** | Analytics consent unwired |
| F-8.5 | Platform Metrics | **Not started** | |

### E-9 Home Cameras (Phase 2) — not started

F-9.1–F-9.5 all **not started** in `apps/family` (phone capture only).

### E-10…E-15 Agency (I-2) — not started

Held at epic/feature level per backlog. Do not staff until B2C pilot data exists and a detailing pass is done.

---

## 3. Sprint model

Assumptions for assignment planning:

- **Sprint length:** 2 weeks  
- **Parallel tracks:** Family app · Detection · Clinician / platform · Docs / compliance  
- **Goal through Sprint 6:** design-partner-ready B2C pilot (not agency, not home cameras, not billing)  
- **Assignee column:** leave blank; fill during planning  
- Story points are relative (1 / 2 / 3 / 5 / 8)

Suggested role tags:

| Tag | Owns |
|---|---|
| `FE-Family` | `apps/family` |
| `FE-Clinician` | `apps/clinician` |
| `BE-Detect` | `action_detection` |
| `BE-Platform` | Supabase migrations, RLS, media/export APIs |
| `Compliance` | Policy copy, COPPA/CHD checklist, trust page content |

---

## 4. Sprint backlog

### Sprint 1 — Pilot blockers: data control & household admin

**Outcome:** A design-partner family can create a household, manage who records, and fully export or delete their data.

| Story | Backlog | Track | Pts | Assignee | Acceptance |
|---|---|---|---|---|---|
| Self-serve household + child creation | F-8.1 | `FE-Family` + `BE-Platform` | 8 | Sakshi | New account can create household and first child without SQL seed |
| Wire “Add someone” / invite household member | F-5.1, F-7.1 | `FE-Family` + `BE-Platform` | 5 | Sakshi | Admin can invite member with role + `can_record`; invitee joins household |
| Family delete everything (verifiable) | F-6.3 | `FE-Family` + `BE-Platform` | 8 | Mateo | Hard-delete household-owned rows; UI confirms rows gone; local IndexedDB cleared |
| Family export everything (metadata + event log) | F-6.3 | `FE-Family` + `BE-Platform` | 5 | Mateo | Export downloads structured log; video policy documented (on-device vs cloud) |
| Retention expiry job (flagged keep, unflagged age out) | F-6.1 | `BE-Platform` | 5 | Mateo | Scheduled job respects `retention_policies`; flagged sessions promoted |

**Exit criteria:** No “toast-only” stubs for export/delete-everything; new family can onboard without engineer seed data.

---

### Sprint 2 — Trust, assent, and share hardening

**Outcome:** Capture and sharing decisions are explicit, auditable, and safe enough for a supervised pilot.

| Story | Backlog | Track | Pts | Assignee | Acceptance |
|---|---|---|---|---|---|
| Formal bystander acknowledgment before first share | F-5.3 | `FE-Family` | 3 | | Share flow blocked until ack recorded |
| Assent UX polish (visible indicator, child pause discoverability) | F-5.2 | `FE-Family` | 3 | | Pause control + recording indicator meet dignity copy |
| Public / in-app trust page (what we record, never do, who sees) | F-8.3 | `FE-Family` + `Compliance` | 5 | | Linked from settings; matches product constraints |
| Clinical export without fragile media-host dependency | F-4.3 | `FE-Clinician` + `BE-Platform` | 8 | | Clinician can download log/PDF for granted scope from app |
| Clip-unit sharing (share episode, not whole session default) | F-3.3 | `FE-Family` + `FE-Clinician` | 5 | | Default grant item is event + lead-in; full session opt-in |

**Exit criteria:** Clinician can review and export from grants alone; family sees clear trust + bystander gates.

---

### Sprint 3 — Detection quality loop

**Outcome:** Appearance detection stays reliable; verification feeds improvement; vocal gap is scoped.

| Story | Backlog | Track | Pts | Assignee | Acceptance |
|---|---|---|---|---|---|
| Verification → labeled feedback store for calibration | F-2.5 | `BE-Detect` + `BE-Platform` | 5 | | Confirm/correct/reject written to queryable feedback table |
| Offline regression pack for family ear/hair/nod takes | F-2.2, F-2.5 | `BE-Detect` | 5 | | Fixture videos + pytest/CI smoke for empty vs mislabel cases |
| Vocal / mand detection spike (scope decision) | F-2.7 | `BE-Detect` | 8 | | Spike doc: ship audio model vs keep stub; go/no-go for Sprint 4 |
| Battery / storage warnings on capture | F-1.4 | `FE-Family` | 3 | | Warn at low storage; block or caution when IndexedDB quota tight |
| Pattern view: simple week/month counts by class | F-3.5 | `FE-Family` | 3 | | Trends screen shows time-bucketed counts (still no ranking) |

**Exit criteria:** Known mislabel/empty-event regressions covered by tests; vocal path decided.

---

### Sprint 4 — Compliance & clinician identity

**Outcome:** Minimum legal/product guardrails for a US design-partner pilot.

| Story | Backlog | Track | Pts | Assignee | Acceptance |
|---|---|---|---|---|---|
| COPPA / child data checklist + in-product disclosures | F-7.5 | `Compliance` + `FE-Family` | 5 | | Checklist in `docs/`; required disclosures before first record |
| Purpose-specific consent unbundling polish | F-7.3 | `FE-Family` | 3 | | Analytics / training / share consents separately togglable and stored |
| Immutable audit log for share/view/export/delete | F-7.6 | `BE-Platform` | 5 | | Append-only `audit_events` (or equivalent) for critical actions |
| Clinician profile basics (org name, verified flag UX) | F-4.5 | `FE-Clinician` + `BE-Platform` | 3 | | Clinician can set org; family sees org on invite |
| Jurisdiction profile stub (US default + disclosure) | F-5.4 | `FE-Family` + `Compliance` | 5 | | Capture settings show jurisdiction; US default disclosures shown |
| If Sprint 3 go: vocal v1 OR document deferral | F-2.7 | `BE-Detect` | 8 | | Either first vocal class in detect path, or explicit backlog deferral |

**Exit criteria:** Pilot legal checklist signed off; audit trail covers share lifecycle.

---

### Sprint 5 — Pilot instrumentation & resilience

**Outcome:** We can measure the pilot without ranking people; capture fails honestly.

| Story | Backlog | Track | Pts | Assignee | Acceptance |
|---|---|---|---|---|---|
| Aggregate platform metrics (households, sessions, events, shares) | F-8.5 | `BE-Platform` | 5 | | Versioned metric definitions; no child-identifying rows in exports |
| Design-partner event instrumentation (consented) | F-8.4 | `FE-Family` + `BE-Platform` | 5 | | Funnel: signup → first capture → first share; gated on analytics consent |
| Detect-down degradation UX | F-1.4, F-2.2 | `FE-Family` | 3 | | Clear stub vs real detect; retry; no silent wrong confidence |
| Exposure controls hardening (manual only, honest copy) | F-5.5 | `FE-Family` + `Compliance` | 3 | | Trust copy matches actual capability (no fake auto-undress claims) |
| Low-light capture guidance | F-2.8 | `FE-Family` | 2 | | Pre-record tip when average luminance low (no ML required) |

**Exit criteria:** Pilot dashboard queryable; trust copy accurate.

---

### Sprint 6 — Pilot polish & freeze

**Outcome:** Stabilize for design-partner use; park Phase 2 / B2B.

| Story | Backlog | Track | Pts | Assignee | Acceptance |
|---|---|---|---|---|---|
| End-to-end pilot script (family + clinician) | — | all | 3 | | Doc + checklist in `docs/`; run once on staging |
| Bug bash / P0 fixes from Sprints 1–5 | — | all | 8 | | No open P0 on capture, detect, share, delete |
| Breach blast-radius review (keys, RLS, no bulk media path) | F-6.4 | `BE-Platform` + `Compliance` | 5 | | Written review; gaps filed as Sprint+ backlog |
| Decision memo: billing (F-8.2), home cameras (E-9), agency detail (I-2) | F-8.2, E-9, E-10+ | PM + eng | 2 | | Explicit defer / date / owner |

**Exit criteria:** Design-partner pilot ready; Phase 2 and agency intentionally deferred.

---

## 5. Deferred (do not assign yet)

| Item | Why deferred |
|---|---|
| **E-9 Home cameras** | Phase 2; backlog sequencing says after mobile capture |
| **F-2.6 Multi-subject group attribution** | Agency problem; family is single-subject |
| **F-7.9 Dual / split household** | Complex custody model; not needed for first pilot households |
| **F-7.2 Encryption & US residency productization** | Infra/ops; track separately with deploy owner |
| **F-8.2 Subscription & billing** | Open product decision |
| **E-10…E-15 Agency platform** | Needs epic detailing pass; procurement timeline is long |
| **True on-device ML (F-1.2 full)** | Large mobile ML investment; local `:8010` is acceptable for pilot if video stays on-device |

---

## 6. Assignment board (fill in planning)

Copy into Linear / GitHub Projects / spreadsheet.

| Sprint | Story (short) | Track | Pts | Assignee | Status |
|---|---|---|---|---|---|
| 1 | Self-serve household + child | FE-Family / BE-Platform | 8 | Sakshi | |
| 1 | Household invite | FE-Family / BE-Platform | 5 | Sakshi | |
| 1 | Delete everything | FE-Family / BE-Platform | 8 | Mateo | |
| 1 | Export everything | FE-Family / BE-Platform | 5 | Mateo | |
| 1 | Retention expiry job | BE-Platform | 5 | Mateo | |
| 2 | Bystander ack | FE-Family | 3 | | |
| 2 | Assent UX polish | FE-Family | 3 | | |
| 2 | Trust page | FE-Family / Compliance | 5 | | |
| 2 | Clinical export | FE-Clinician / BE-Platform | 8 | | |
| 2 | Clip-unit sharing default | FE-Family / FE-Clinician | 5 | | |
| 3 | Verification feedback store | BE-Detect / BE-Platform | 5 | | |
| 3 | Detect regression pack | BE-Detect | 5 | | |
| 3 | Vocal spike | BE-Detect | 8 | | |
| 3 | Battery / storage warnings | FE-Family | 3 | | |
| 3 | Pattern time buckets | FE-Family | 3 | | |
| 4 | COPPA checklist + disclosures | Compliance / FE-Family | 5 | | |
| 4 | Consent unbundling polish | FE-Family | 3 | | |
| 4 | Immutable audit log | BE-Platform | 5 | | |
| 4 | Clinician org profile | FE-Clinician / BE-Platform | 3 | | |
| 4 | Jurisdiction stub | FE-Family / Compliance | 5 | | |
| 4 | Vocal v1 or deferral | BE-Detect | 8 | | |
| 5 | Platform metrics | BE-Platform | 5 | | |
| 5 | Design-partner funnel events | FE-Family / BE-Platform | 5 | | |
| 5 | Detect-down UX | FE-Family | 3 | | |
| 5 | Honest exposure copy | FE-Family / Compliance | 3 | | |
| 5 | Low-light tip | FE-Family | 2 | | |
| 6 | Pilot script | all | 3 | | |
| 6 | Bug bash P0 | all | 8 | | |
| 6 | Blast-radius review | BE-Platform / Compliance | 5 | | |
| 6 | Deferral decision memo | PM + eng | 2 | | |

**Capacity hint (3 engineers):** ~26–34 pts/sprint is comfortable if one owns detect, one family FE, one platform/clinician.

---

## 7. Suggested team split

| Developer seat | Primary track | Owns sprints focus |
|---|---|---|
| Dev A (**Sakshi**) | `FE-Family` | Sprint 1: S1-T02 → S1-T01 |
| Dev B (**Mateo**) | `BE-Platform` | Sprint 1: S1-T04 → S1-T03 → S1-T05 |
| Dev C | `BE-Detect` | Regression pack, verification feedback, vocal spike |
| Shared / PM | `Compliance` | COPPA checklist, trust copy, jurisdiction, deferral memo |

---

## 8. Definition of done (per story)

- Acceptance criteria above met  
- Migration applied if schema changes (`supabase/migrations/`)  
- Docs updated (`README.md` / `docs/detection.md` / this file’s sprint notes if scope shifts)  
- No new toast-only stubs for user-facing destructive or export actions  
- Family path remains **post-capture only** (no live `/api/frame` from handheld UI)

---

## 9. Next planning step

1. Assign names in §6.  
2. Pull Sprint 1 stories into the tracker.  
3. Confirm design-partner geography (affects F-5.4 / F-7.5 priority).  
4. Revisit this doc after Sprint 2 with actual velocities.

# Product loop / integration map

> Navigation: [Docs Index](../README.md) / Product / **Product loop map**  
> **F-ID status spreadsheet (source of truth):** [ATMON_Capability_Matrix.csv](ATMON_Capability_Matrix.csv)  
> Open the CSV in Excel / Google Sheets for capability → use case → AC → owners → dates → status.

## Status rollup (from CSV · 2026-10-05)

| Status | Count |
|---|---:|
| Done | 11 |
| Partial | 24 |
| Not started | 5 |
| Deferred | 6 |
| **Total rows** | **46** |

(Includes F-1.1…F-8.5 plus E-9 and E-10+ Deferred rollups.)

## Owner legend

| Role | People |
|---|---|
| Impl_Owner | Sakshi (FE S1 + ML), Mateo (FE/BE), Abhishek (pilot script/hosting tasks), Mainak (blast-radius / memo) |
| Hosting_Owner | **Abhishek** by default on cloud/shared hosts; **Mainak** only when Notes override |
| QA | Default **Abhishek** (acceptance / E2E against Acceptance_Criteria); blank on Deferred rollups |
| QA_Status | `Pass` / `Pending` / `Fail` / `N/A` |

Acceptance_Criteria rows use testable **PASS when / FAIL if** language (purposes, storage tables, tags named where relevant).

Audit: `make validate-capability-matrix` (also part of `make validate-local`).

---

This document is the **journey / integration seam map** (UI → API → tables). It is **not** the F-ID status ledger — use the CSV for that.

| Journey | Family / clinician surface | Client API / call | Backend / table | Notes |
|---|---|---|---|---|
| Sign in | Family / Clinician auth screens | Supabase Auth | `auth.users`, `profiles` | Email/password |
| Household membership | Family settings / members | `loadHousehold` etc. | `household_members` | Roles + `can_record` |
| Child record | Family home | guardianship resolve | `children`, `child_guardianships` | Single-household path today |
| Record take | Family record screen | `PhoneRecorder` / IndexedDB | local only until save | Pre-roll, assent pause |
| Post-capture detect | Family processing | `POST :8010/api/detect/video` | `action_detection` | ADR-001 |
| Persist session | Family save | `saveCapture` | `sessions`, `events`, `session_segments`, `assent_events` | RLS recorder |
| Verify event | Family / Clinician event UI | `verifyEvent` / `judgeEvent` | `event_verifications` | confirm / correct / reject |
| Flag for share | Family session | flag fields on events | `events` | Feeds clinician queue |
| Share grant | Family share sheet | `createGrant` / `updateGrant` | `share_grants`, `share_grant_items` | Scope + expiry |
| Clinician review | Clinician queue / player | grant-scoped selects | RLS on sessions/events | No detect call |
| Access transparency | Family access screen | aggregates | `access_log` | viewed / exported / downloaded |
| Delete session | Family session delete | `deleteSession` | DELETE RLS + IndexedDB | migration `20261004190000_*` |
| Retention prefs | Family settings | `saveRetention` | `retention_policies` | Expiry job still open |
| Clinical export | Clinician export | media host / PDF | `exports` | Needs media URL path |

## Detector classes (live product)

| Class | Detected by X-CLIP bank | UI label |
|---|---|---|
| `ear_cover` | yes | Covering ears |
| `hair_twirling` | yes | Hair twirling |
| `head_nodding` | yes | Head nodding |
| `vocal` / `mand` / … | stub / taxonomy only | Legacy / fallback |

## Out of matrix (deferred)

Home cameras (E-9), agency multi-tenancy (E-10+), dual-household guardianship,
billing, true on-device ML. See [BACKLOG.md](../sprints/BACKLOG.md) and Deferred rows in the CSV.

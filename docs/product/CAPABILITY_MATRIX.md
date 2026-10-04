# Capability Matrix

> Navigation: [Docs Index](../README.md) / Product / **Capability Matrix**

Slim end-to-end map for the ATMON product loop. Not exhaustive of every UI
string — focus is assignable ownership and integration seams.

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
billing, true on-device ML. See [BACKLOG.md](../sprints/BACKLOG.md).

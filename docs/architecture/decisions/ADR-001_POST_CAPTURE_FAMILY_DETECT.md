# ADR-001: Post-capture family detection (no live frame pump)

| Field | Value |
|---|---|
| Status | Accepted |
| Date | 2026-10-04 |
| Context | Family handheld capture UX + `action_detection` service |

## Decision

The family Vite app scores takes **only after recording stops** via
`POST /api/detect/video`. It does **not** stream frames to `/api/frame` or use
`/api/live/*` while the parent is recording.

Live detection APIs in `action_detection/webapp` remain available for the
**detector demo UI only**.

## Consequences

- Recording screen shows camera, timer, and controls only (no live event marks).
- Events appear after stop on processing → details (`Saving…` while scoring).
- Family thresholds use a softened pipeline (`soften_for_family_detect`, raw
  floor merge, refine) documented in [detection.md](../../detection.md).
- Identity gallery stays off for unsupervised family post-capture.

## Alternatives considered

- Live frame pump during record (removed; higher battery/privacy cost and
  noisier UX for parents).
- Cloud-only inference (rejected for default path; video stays on-device).

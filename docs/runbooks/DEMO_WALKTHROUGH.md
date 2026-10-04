# Demo Walkthrough — Family → Clinician

> Navigation: [Docs Index](../README.md) / Runbooks / **Demo**  
> Stack: [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md)

---

## Setup

1. Detect service on `:8010` with prototype bank present.
2. Family app on `:5173` signed in as a household member with `can_record`.
3. Clinician app on `:5180` signed in as an invited clinician (active share grant).

---

## Script

### A. Family capture

1. Open Family → **Record now**.
2. Tap subject on preview (or accept largest-face default).
3. Record a short ear-cover or hair-twirl take (10–20s).
4. Stop — badge shows **Saving…** while `POST /api/detect/video` runs.
5. Confirm events appear on processing / details (not during record).

### B. Verify & share

1. Open an event → confirm or correct label.
2. Flag for clinician if needed.
3. Create / confirm share grant (scoped, time-limited).
4. Optional: **Delete this session** to show recorder delete path.

### C. Clinician review

1. Open Clinician → review queue shows granted / flagged items.
2. Play clip; seek to event onset (antecedent lead-in).
3. Judge / note as appropriate.
4. If media host configured: export log/PDF for granted scope.

---

## What not to demo as product

- Live detector demo UI (`action_detection` Live tab) — separate from family.
- Legacy `python -m src.server` HUD dashboard.
- Home cameras or agency multi-room (not built).

---

## Failure demos (optional)

| Action | Expected |
|---|---|
| Stop detect service before save | Toast + stub `detector_version: stub` |
| Quiet / empty take | `events: []` with real detector can be normal |

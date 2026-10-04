# DESIGN.md — ATMON UI Design SSOT

> Read this before any visual or copy change in `apps/family` or `apps/clinician`.
> When this file disagrees with live CSS, **CSS wins**.

---

## Authority

| Surface | Token / style source |
|---|---|
| Family app | `apps/family` CSS / component styles (Vite React) |
| Clinician app | `apps/clinician` CSS / component styles |
| Detector demo UI | `action_detection/DESIGN.md` + `action_detection/webapp/ui` |

Do not invent a second purple-gradient or generic AI-dashboard look. Match the
existing app language unless the user requests a redesign.

---

## Product copy rules

1. **Neutral taxonomy** — log self-regulating behavior without pathologizing
   language (prefer “self-regulating” / class labels over “problem” / “abnormal”).
2. **No scores or ranks** of children, parents, or caregivers in the UI.
3. **Honest capability claims** — do not advertise automated undress/toilet
   detection or live family scoring if the code path does not exist.
4. **Assent & dignity** — recording indicator visible; pause control discoverable;
   no covert capture mode.

---

## Interaction notes

- Family record: camera, timer, controls only during take (no live event chips).
- After stop: **Saving…** while post-capture detect runs.
- Destructive actions (delete session / delete everything) require a confirm sheet
  and must not be toast-only stubs when claimed complete.

---

## Changing the system

Propose token or copy changes in the PR body. Prefer small CSS variable updates
over one-off magic hex values scattered in JSX.

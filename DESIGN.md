# Design System — Action Detector

## Product Context
- **What this is:** Browser demo for few-shot action detection (record refs → build bank → live detect → review clips).
- **Who it's for:** Researchers / engineers iterating on reference clips and live detection.
- **Space/industry:** Assistive / behavioral sensing tooling (internal demo).
- **Project type:** Internal web app / demo tool (not a marketing site).

## Aesthetic Direction
- **Direction:** Apple-adjacent utilitarian — calm, direct, materials over chrome.
- **Decoration level:** Minimal (flat system background + translucent sticky chrome).
- **Mood:** Quiet confidence; the camera and detections are the hero, not the UI.

## Typography
- **Display/UI/Body:** SF / system stack (`-apple-system`, BlinkMacSystemFont, system-ui)
- **Data/Feed:** SF Mono / ui-monospace
- **Rationale:** Platform optical sizing and tracking; this is a tool UI, not a brand landing page.
- **Scale:** Brand ~1.15rem / -0.02em; panel titles ~1.05–1.2rem; body ~0.9rem; meta ~0.8rem.

## Color
- **Approach:** Restrained — one accent + neutrals + semantic only.
- **Background:** `#f2f2f7` (iOS system grouped background)
- **Surface:** `#ffffff`
- **Fill:** `#e5e5ea` / input `#f2f2f7`
- **Text:** `#1c1c1e` / secondary `#636366`
- **Accent:** `#007aff` (system blue); press `#0066d6`
- **Semantic:** ok `#34c759`, warn `#ff9500`, danger `#ff3b30`
- **Not used:** gradient brand text, multi-hue ambient washes, per-class rainbow swatches, violet/indigo/teal accents
- **Dark mode:** Not shipped (demo stays light).

## Spacing
- **Base unit:** 8px
- **Density:** Comfortable
- **Radius:** sm 10px / panel 14px

## Layout
- **Approach:** Stage-first single page — Live (hero) → References | Captures side-by-side on wide screens
- **Max content width:** 960px
- **Grouping:** Actions live next to what they affect (Rebuild → References; Refresh/Delete → Captures)
- **Order rationale:** Camera is the product; teach/review support it below
- **Not used:** Segmented tabs / multi-route chrome for core flows

## Motion
- **Approach:** Minimal-functional
- **Press feedback:** `scale(0.97)` on `:active` (~100ms ease-out)
- **Respect:** `prefers-reduced-motion` / `prefers-reduced-transparency`

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-04 | Apple system type + iOS blue accent | Native-feeling tool UI |
| 2026-09-04 | Sticky translucent topbar | Wayfinding without opaque chrome |
| 2026-09-04 | Live first; References + Captures beside | Stage-first hierarchy; controls mapped to sections |
| 2026-09-04 | Strip multi-hue wash / gradient brand / class rainbow / tabs | Looked AI-generated; restore restrained single-page tool |
| 2026-09-04 | Analytics tab parked | Revisit later; keep detection workflow uncluttered |
| 2026-09-21 | React (Vite) SPA in `webapp/ui`, build → `webapp/static` | Same DESIGN.md tokens; Apple HIG tool layout (sticky glass topbar, stage-first Live, system blue primary) |

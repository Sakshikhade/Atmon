# Design System — Action Detector

## Product Context
- **What this is:** Browser demo for few-shot action detection (record refs → build bank → live detect → review clips).
- **Who it's for:** Researchers / engineers iterating on reference clips and live detection.
- **Space/industry:** Assistive / behavioral sensing tooling (internal demo).
- **Project type:** Internal web app / demo tool (not a marketing site).

## Aesthetic Direction
- **Direction:** Ocean Breeze elegance — calm ice atmosphere, floating white panels, navy primary.
- **Decoration level:** Soft (tinted shadows + subtle radial atmosphere; no blobs or card mosaics).
- **Mood:** Quiet confidence; the camera and detections are the hero, not the UI.
- **Signature:** Ice page wash + frosted topbar + navy CTAs framing a dark camera stage.

## Typography
- **Display/UI/Body:** SF / system stack (`-apple-system`, BlinkMacSystemFont, system-ui)
- **Data/Feed:** SF Mono / ui-monospace
- **Rationale:** Platform optical sizing; tool UI, not a brand landing page.
- **Scale:** Brand ~1.2rem / -0.03em; panel titles ~1.05–1.25rem; body ~0.9rem; meta chips ~0.75rem.

## Color
- **Palette:** Ocean Breeze (04) — Calm / Fresh / Modern
- **Background:** `#E8F6FF` with soft sky/mint radial atmosphere
- **Surface:** `#FFFFFF`
- **Fill:** `#CFE9FB` / input `#F4FAFF`
- **Text:** `#0A2A5C` / secondary `#3D5A73`
- **Primary:** `#0B3D91` (brand, titles, CTAs); press `#072E70`
- **Secondary:** `#3BA7F2` (live/busy signal, focus rings, ghost hover — not body text)
- **Tertiary:** `#7FE7D6` (Ready fills); ink `#0B6B5C`
- **Semantic:** warn `#C47A00` / danger `#D9382B`
- **Contrast notes:** White on primary ~10:1; secondary fails as small text on white (~2.6:1) so signals only
- **Dark mode:** Not shipped (demo stays light).

## Spacing
- **Base unit:** 8px
- **Density:** Comfortable
- **Radius:** sm 12px / panel 18px / chip 999px

## Layout
- **Approach:** Stage-first single page — Live (hero) → References | Captures side-by-side on wide screens
- **Max content width:** 1040px
- **Grouping:** Actions live next to what they affect
- **Not used:** Segmented tabs / multi-route chrome for core flows

## Motion
- **Approach:** Minimal-functional
- **Press feedback:** `scale(0.98)` on `:active` (~120ms ease-out)
- **Entrance:** Soft panel rise (opacity + 6px) once; gated by `prefers-reduced-motion`
- **Respect:** `prefers-reduced-motion` / `prefers-reduced-transparency`

## Decisions Log
| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-09-04 | Apple system type + stage-first Live | Native-feeling tool UI |
| 2026-09-04 | Sticky translucent topbar | Wayfinding without opaque chrome |
| 2026-09-04 | Strip multi-hue wash / rainbow / tabs | Avoid AI-slop look |
| 2026-09-21 | React (Vite) SPA in `webapp/ui` | Same tokens; build → `webapp/static` |
| 2026-09-21 | Ocean Breeze full UI redesign | User ask: elegant / eye-appealing; navy CTA + ice atmosphere |

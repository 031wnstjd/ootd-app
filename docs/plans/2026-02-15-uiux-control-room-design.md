# UI/UX Design: OOTD Control Room

Date: 2026-02-15
Scope: frontend theme and dashboard UX refresh

## Goal
- Priority: visual quality first
- Direction: Modern Control Room
- Coverage: main page + global tone
- Added requirement: light/dark theme toggle with persistence

## Information Architecture
- Hero: product identity, API environment, and key KPIs in one panel.
- Main flow: Step 1 (create) and Step 2 (live status) remain primary and above the fold.
- Results: Step 3 remains dedicated and visually separated.
- Operations: catalog ops, metrics, history stay as drawers with clearer hierarchy.

## Visual System
- Typography: `Space Grotesk` for headings, `DM Sans` for body.
- Background: layered gradient control-room backdrop.
- Surfaces: glass-like cards with subtle blur and border highlights.
- States: unified status chips and color semantics for success/warn/error.
- Motion: short card enter animation for dashboard sections.

## Theme Toggle
- Storage key: `ootd_theme_v1`
- Implementation: set `document.documentElement[data-theme]` to `light` or `dark`.
- Initial mode: localStorage first, otherwise system preference.

## Validation
- `npm run typecheck`
- `npm run lint`
- `npm run build`
- Manual checks: create/refresh/approve/retry/publish/history select + theme persistence

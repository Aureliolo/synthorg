---
title: UX Design Guidelines
description: Authoritative design guidelines for the SynthOrg web dashboard. Covers colour system, component patterns, interaction design, animation language, accessibility, and responsive breakpoints.
---

# UX Design Guidelines

This document is the single source of truth for all dashboard page implementations. It codifies the decisions from the design exploration (#765, Warm Ops winner) and page structure (#766) into concrete, implementable specifications.

**Prerequisite reading**: [Brand Identity & UX Design System](brand-and-ux.md) for rationale behind each decision. This document covers the *what*; brand-and-ux.md covers the *why*.

---

## 1. Brand Identity

### 1.1 Colour System

Colours are **state-driven**, not decorative. Every coloured element answers: "what is the system telling me?"

#### Semantic Colour Tokens

| Token | Hex | Role | Usage |
|-------|-----|------|-------|
| `accent` | `#38bdf8` | Brand, interactive, focus | Links, active nav, focus rings, brand identity |
| `accent-dim` | `#0ea5e9` | Muted brand, secondary interactive | Hover states, secondary buttons, onboarding |
| `success` | `#10b981` | Rising, healthy, completed | Metrics trending up, tasks done, agents active |
| `warning` | `#f59e0b` | Declining, attention needed | Metrics trending down, budget nearing limit |
| `danger` | `#ef4444` | Critical, error, immediate action | Agent errors, budget exceeded, failed tasks |
| `text-primary` | `#e2e8f0` | Main content text | Headings, values, primary content |
| `text-secondary` | `#94a3b8` | Supporting text | Labels, descriptions, secondary info |
| `text-muted` | `#8b95a5` | Least prominent text | Timestamps, metadata, disabled items |
| `bg-base` | `#0a0a12` | Page background | Deepest background layer |
| `bg-surface` | `#0f0f1a` | Sidebar, elevated surfaces | Sidebar, panels, raised areas |
| `bg-card` | `#13131f` | Card backgrounds | All card containers |
| `bg-card-hover` | `#181828` | Card hover state | Card background on mouse-over |
| `border` | `#1e1e2e` | Default borders | Card borders, dividers, separators |
| `border-bright` | `#2a2a3e` | Interactive/hover borders | Focus rings, hover states, active borders |

All hex values are WCAG AA verified (see [Section 5.1](#51-wcag-aa-contrast-matrix)).

#### Dynamic Colour Assignment

Metric cards, sparklines, and trend indicators assign colour by data direction:

| Data state | Colour token | Visual meaning |
|------------|-------------|----------------|
| Improving / rising | `success` | Green: things getting better |
| Stable / normal | `accent` or `text-muted` | Neutral: no action needed |
| Declining / degrading | `warning` | Amber: attention warranted |
| Critical / threshold | `danger` | Red: act now |

#### Dark Mode Only

The dashboard is dark-mode only (confirmed in #762). All colour tokens assume dark backgrounds. No light mode is planned. WCAG AA ratios are validated against bg-base, bg-surface, bg-card, and bg-card-hover.

### 1.2 Typography Scale

#### Font Families

| Role | Font | Loaded via |
|------|------|------------|
| Monospace | Geist Mono | `@fontsource-variable/geist-mono` (self-hosted) |
| Sans-serif | Geist Sans | `@fontsource-variable/geist` (self-hosted) |

All fonts self-hosted via `@fontsource`; no external CDN dependencies.

#### Type Scale

| Role | Font | Size | Weight | Line height | Letter spacing | Example |
|------|------|------|--------|-------------|----------------|---------|
| Page heading | Sans | 18px (`text-lg`) | 600 | 1.5 | -0.01em | "Overview" |
| Section heading | Sans | 13px | 600 | 1.4 | 0 | "Org Health" |
| Section sublabel | Sans | 11px | 400 | 1.4 | 0 | "Department performance" |
| Body text | Sans | 13px (`text-sm`) | 400 | 1.5 | 0 | Descriptions, paragraphs |
| Small text | Sans | 12px (`text-xs`) | 400 | 1.5 | 0 | Secondary info, sublabels |
| Label (uppercase) | Sans | 11px | 500 | 1.2 | 0.06em | "TASKS TODAY" |
| Metric value | Mono | 26px | 700 | 1.0 | -0.02em | "24", "$42.17" |
| Data value | Mono | 12px | 600 | 1.4 | 0 | "87%", "$12.50" |
| Timestamp | Mono | 10px | 400 | 1.2 | 0 | "2m ago", "15:42 UTC" |
| Code / agent name | Mono | 12px | 400 | 1.4 | 0 | "agent-cfo-001" |
| Change badge | Mono | 11px | 500 | 1.2 | 0 | "+12%", "-3.2%" |

**Rule**: Numbers and data always use monospace. Labels and descriptions always use sans-serif.

!!! abstract "Currency in examples"

    Dollar signs in the examples above illustrate the shape of a money label; runtime rendering uses the configured `budget.currency` resolved by settings/fallback policy. Helpers in `web/src/utils/format.ts` (`formatCurrency`, `formatCurrencyCompact`) apply the locale-specific symbol and grouping.

### 1.3 Spacing Grid

Base unit: **8px**. All spacing values follow a 4px sub-grid (half the 8px base unit).

| Token | Value | Usage |
|-------|-------|-------|
| `space-1` | 4px | Icon-to-label gap, tight inline spacing |
| `space-2` | 8px | Intra-component gaps (e.g. between label and value) |
| `space-3` | 12px | Card padding (dense density), grid gap (dense) |
| `space-4` | 16px | Card padding (balanced density), grid/section gap (balanced) |
| `space-5` | 20px | Card padding (sparse density) |
| `space-6` | 24px | Page padding, section gap (sparse), major separations |
| `space-8` | 32px | Page section breaks |
| `space-10` | 48px | Major layout divisions |
| `space-12` | 64px | Major layout divisions, sidebar expanded padding |

### 1.4 Logo and Wordmark Usage

| Rule | Spec |
|------|------|
| Placement | Top-left of sidebar, vertically centred in the brand zone |
| Minimum size | 20px height for icon mark, 80px width for wordmark |
| Clear space | Minimum 8px (1 base unit) on all sides |
| Collapsed sidebar | Icon mark only, centred in the 56px rail |
| Expanded sidebar | Icon mark + wordmark, left-aligned with 16px left padding |
| Colour | `text-primary` (`#e2e8f0`) on `bg-surface` background |
| Never | Do not tint the logo with accent colour; do not animate the logo |

### 1.5 Visual Signatures

These 3 elements create instant "this is SynthOrg" recognition:

1. **Warm soft blue accent** (`#38bdf8`): brand-neutral so state colours dominate, but present in links, focus rings, active nav, and sparkline defaults
2. **Dark cards with subtle borders**: `bg-card` (`#13131f`) with 1px `border` (`#1e1e2e`), 8px border-radius. Cards float above `bg-base` without heavy shadows
3. **Monospace data values**: Geist Mono for all numbers, metrics, agent names, and timestamps creates a "control room" feel where data is always legible and aligned

---

## 2. Component Patterns

### 2.1 Card Anatomy

All cards share a consistent structure:

```text
+----------------------------------------------+
|  [icon]  Title                     [status *] |  <- Header (12px vertical padding, 16px horizontal)
|----------------------------------------------|  <- 1px border-bottom (border token)
|                                              |
|  Content area                                |  <- 14-16px padding (density-dependent)
|  (metrics, text, charts, or mixed)           |
|                                              |
|----------------------------------------------|  <- Optional: 1px border-top
|  Footer: secondary info        timestamp     |  <- 10-12px vertical padding
+----------------------------------------------+

Card specs:
  Background:    bg-card (#13131f)
  Border:        1px solid border (#1e1e2e)
  Border-radius: 8px (--radius-lg)
  Padding:       16px (balanced density)
  Hover:         bg-card-hover (#181828), translateY(-1px),
                 box-shadow: 0 4px 24px accent/8%
```

#### Card states

| State | Background | Border | Transform | Shadow |
|-------|-----------|--------|-----------|--------|
| Default | `bg-card` | `border` | none | none |
| Hover | `bg-card-hover` | `border` | translateY(-1px) | 0 4px 24px accent at 8% opacity |
| Active/selected | `bg-card` | `accent-dim` | none | none |
| Disabled | `bg-card` at 60% opacity | `border` | none | none |

### 2.2 Status Encoding Rules

Status is **never communicated by colour alone**. The encoding hierarchy:

| Level | What to add | When |
|-------|-------------|------|
| 1. Colour | State colour (success/warning/danger/accent) | Always; base layer |
| 2. Shape | Dot (6px circle) or icon (Lucide) | Always alongside colour |
| 3. Text label | "Active", "Error", "Warning" | For critical states and when space allows |
| 4. Animation | Pulse or flash | Only during state transitions, never steady-state |

#### Status dot spec

| Property | Value |
|----------|-------|
| Size | 6px diameter circle |
| Placement | Right-aligned in card header, vertically centred |
| Colours | `success` (active/healthy), `warning` (attention), `danger` (error/critical), `text-muted` (inactive/idle) |
| Border | None (solid fill) |
| Pulse | 2s ease-in-out infinite, only on state *transition* (stops after 3 cycles) |

### 2.3 Data Display Specs

#### Sparkline

| Property | Value |
|----------|-------|
| Default size | 64 x 24px |
| Metric card size | 60 x 28px |
| Stroke width | 1.5px |
| Stroke cap/join | round / round |
| Fill | Linear gradient, top: stroke colour at 30% opacity, bottom: 0% opacity |
| End dot | 2px radius circle, solid stroke colour |
| Draw animation | stroke-dasharray 200, 1s ease forwards, 200ms delay |
| Colour | Dynamic; follows data state (success/accent/warning/danger) |

#### Progress bar

| Property | Value |
|----------|-------|
| Height | 2px (inline metric), 6px (department health) |
| Border-radius | 1px (2px bar) or 3px (6px bar) |
| Track colour | `border` (`#1e1e2e`) |
| Fill colour | Dynamic; follows data state |
| Fill animation | 900ms cubic-bezier(0.4, 0, 0.2, 1) |
| Glow | 6px health bar only: `0 0 8px accent/30%` when healthy |

#### Gauge (arc)

| Property | Value |
|----------|-------|
| Arc angle | 180 degrees (half-circle, bottom open) |
| Outer radius | 48px (default), 32px (compact) |
| Stroke width | 6px |
| Track colour | `border` (`#1e1e2e`) |
| Fill colour | Dynamic; follows data state (success/accent/warning/danger) |
| Fill animation | 900ms cubic-bezier(0.4, 0, 0.2, 1), clockwise from left |
| Value label | Centred inside the arc, Geist Mono 18px weight 700, `text-primary` |
| Sub-label | Below value label, 11px, `text-muted` (e.g. "of 100") |
| Tick marks | Optional, 1px lines at 0%, 25%, 50%, 75%, 100% positions, `border` colour |

#### Change badge

| Property | Value |
|----------|-------|
| Font | Mono, 11px, weight 500 |
| Padding | 2px 6px |
| Border-radius | 4px |
| Positive | Colour: `success`, bg: `rgba(16, 185, 129, 0.08)`, border: `rgba(16, 185, 129, 0.2)` |
| Negative | Colour: `danger`, bg: `rgba(239, 68, 68, 0.08)`, border: `rgba(239, 68, 68, 0.2)` |

### 2.4 MetricCard Layout

```text
+----------------------------------------------+
|  TASKS TODAY              [sparkline 60x28]  |  <- Label: 11px uppercase, 0.06em spacing
|  24                                          |  <- Value: 26px mono, weight 700
|  ========== (progress bar, optional)         |  <- 2px height, full width
|  of 30 completed               +12%         |  <- Sub: 12px / Change badge: 11px mono
+----------------------------------------------+
```

| Element | Style |
|---------|-------|
| Label | 11px, uppercase, letter-spacing 0.06em, `text-muted` colour |
| Value | 26px, Geist Mono, weight 700, `text-primary`, letter-spacing -0.02em |
| Sparkline | 60 x 28px, top-right aligned, colour follows data state |
| Sub-text | 12px, `text-muted` or state colour when indicating threshold |
| Change badge | Bottom-right, styled per change badge spec above |
| Progress bar | Optional, below value, full card width minus padding |

### 2.5 AgentCard Layout

```text
+----------------------------------------------+
|  [avatar]  Agent Name              [* dot]   |  <- Name: 13px sans weight 600
|            Software Engineer                 |  <- Role: 12px, text-secondary
|----------------------------------------------|
|  Dept: Engineering     Task: Fix auth bug    |  <- 12px, text-secondary
|                                   2m ago     |  <- 10px mono, text-muted
+----------------------------------------------+
```

| Element | Style |
|---------|-------|
| Avatar | 32px circle, initials on `accent-dim` background |
| Name | 13px, sans, weight 600, `text-primary` |
| Role | 12px, sans, weight 400, `text-secondary` |
| Status dot | 6px, right of header, colour by agent status |
| Department | 12px, `text-secondary`, label prefix in `text-muted` |
| Current task | 12px, `text-secondary`, truncated with ellipsis |
| Timestamp | 10px, Geist Mono, `text-muted`, bottom-right |

The AgentCard layout must be **identical** across the Agents page, Org Chart nodes, Dashboard agent list, and any other surface showing agents.

---

## 3. Interaction Design

### 3.1 Progressive Disclosure Levels

| Level | Surface | Content | Trigger |
|-------|---------|---------|---------|
| L0: Summary | Card or table row | Key metric + status indicator | Always visible |
| L1: Tooltip | Floating overlay | Extended detail, no navigation | Hover (300ms delay) |
| L2: Expand | Inline panel or slide-in | Full detail with actions | Click |
| L3: Full page | Dedicated route | Complete view with sub-navigation | Click-through link or Cmd+K |

**Rules**:

- L0 must be scannable in < 1 second (3-5 data points maximum per card)
- L1 tooltips must never contain interactive elements (links, buttons)
- L2 panels are URL-addressable for deep linking (e.g. `/agents/{agentId}`)
- L3 navigation always creates a browser history entry

### 3.2 Hover Behaviour

| Component | Hover effect | Transition |
|-----------|-------------|------------|
| Card | Background shifts to `bg-card-hover`, translateY(-1px), accent shadow | 200ms ease |
| Table row | Background shifts to `bg-card-hover` | 150ms ease |
| Link | Underline appears (text-decoration) | instant |
| Button (primary) | Background darkens 10% | 150ms ease |
| Button (ghost) | Background appears at `bg-card-hover` | 150ms ease |
| Nav item | Background to `rgba(255, 255, 255, 0.04)`, text to `text-primary` | 200ms ease |
| Nav item (active) | Background `accent/6%`, text `accent`, left border `accent` (2px) | 200ms ease |

### 3.3 Inline Editing

For settings values, agent names, and editable fields:

| Action | Behaviour |
|--------|----------|
| Activate | Click on value; field becomes editable input |
| Visual cue | Subtle border appears around field, background lightens to `bg-surface` |
| Save | Enter key or blur (focus loss) |
| Cancel | Escape key; reverts to previous value |
| Validation | Inline error message below field in `danger` colour |
| Loading | Input disabled, spinner replaces save icon |
| Success | Brief flash of `success/10%` background, then fade |

### 3.4 Drag-and-Drop

For task board kanban columns and org chart hierarchy view:

| Phase | Behaviour |
|-------|----------|
| Grab | Cursor changes to `grabbing`, card lifts (scale 1.02, shadow deepens) |
| Drag | Semi-transparent ghost preview follows cursor, original position shows dashed border placeholder |
| Over drop zone | Drop zone border changes to `accent`, background to `accent/5%` |
| Drop | Card settles into position with spring animation (stiffness 300, damping 30) |
| Invalid drop | Card springs back to original position |

### 3.5 Command Palette (Cmd+K)

Built with the `cmdk-base` library (cmdk port on Base UI Dialog).

| Property | Spec |
|----------|------|
| Trigger | Cmd+K (macOS) / Ctrl+K (Windows/Linux) |
| Dismiss | Escape, click outside, or Cmd+K again |
| Background | Modal overlay at `bg-base/80%` backdrop blur |
| Panel | `bg-surface`, `border-bright` border, 12px border-radius, max-width 640px |
| Search input | 16px, `text-primary`, no border, `bg-surface` background |

#### Scope behaviour

| Context | Scope | Result types |
|---------|-------|--------------|
| Any page | Global | Pages, agents, tasks, settings namespaces |
| Task Board | Page-local | Tasks (filtered by current board filters) |
| Agents page | Page-local | Agents (name, role, department) |
| Settings | Page-local | Setting keys within current namespace |

#### Keyboard navigation

| Key | Action |
|-----|--------|
| Arrow Up/Down | Navigate results |
| Enter | Select highlighted result |
| Tab | Switch between scope (global/page-local) |
| Escape | Close palette |

---

## 4. Animation Language

### 4.1 Motion Presets

All animation values are defined in `web/src/lib/motion.ts` and imported as constants. Never hardcode animation values in components.

#### Spring presets

| Preset | Config | Use case |
|--------|--------|----------|
| `springDefault` | `{ type: "spring", stiffness: 300, damping: 30, mass: 1 }` | General-purpose: modals, panels, card interactions |
| `springGentle` | `{ type: "spring", stiffness: 200, damping: 25, mass: 1 }` | Subtle movements: tooltips, dropdowns |
| `springBouncy` | `{ type: "spring", stiffness: 400, damping: 20, mass: 0.8 }` | Playful feedback: drag-drop settle, success states |
| `springStiff` | `{ type: "spring", stiffness: 500, damping: 35, mass: 1 }` | Snappy responses: toggles, switches |

#### Tween presets

| Preset | Config | Use case |
|--------|--------|----------|
| `tweenDefault` | `{ type: "tween", duration: 0.2, ease: [0.4, 0, 0.2, 1] }` | Hover states, colour changes, opacity |
| `tweenSlow` | `{ type: "tween", duration: 0.4, ease: [0.4, 0, 0.2, 1] }` | Page transitions, large layout shifts |
| `tweenFast` | `{ type: "tween", duration: 0.15, ease: "easeOut" }` | Micro-interactions, button press |
| `tweenExitFast` | `{ type: "tween", duration: 0.15, ease: "easeIn" }` | Panel/drawer exit, collapse animations |

### 4.2 Page Transitions

| Property | Value |
|----------|-------|
| Exit | Opacity 1 -> 0, x: 0 -> -8px, `tweenExitFast` |
| Enter | Opacity 0 -> 1, x: 8px -> 0, duration 200ms, `tweenDefault` |
| Direction | Content slides in the direction of navigation (deeper = right, back = left) |

### 4.3 Card Entrance

Cards stagger their entrance when a page loads or data first arrives.

| Property | Value |
|----------|-------|
| Initial state | `{ opacity: 0, y: 8 }` |
| Animate to | `{ opacity: 1, y: 0 }` |
| Transition | `tweenDefault` (200ms) |
| Stagger | 30ms between consecutive cards |
| Stagger note | Consuming components should cap visible stagger at ~10 items (300ms) to avoid long entrance sequences |

### 4.4 Status Change Animation

When a value updates in real-time (via WebSocket):

| Phase | Duration | Effect |
|-------|----------|--------|
| Flash | 200ms | Background flashes `accent/10%` (or relevant state colour at 10%) |
| Hold | 100ms | Holds the flash colour |
| Fade | 300ms | Fades back to default background |

**No animation** on initial page load; only on subsequent real-time updates after the page is settled.

### 4.5 Real-Time Update Feedback

| Element | Behaviour |
|---------|----------|
| Metric value | Number transitions with counting animation (200ms) |
| Sparkline | New data point appends with draw animation |
| Timestamp | Text updates, brief `accent/10%` flash |
| Badge count | Increment with scale bounce (1.0 -> 1.15 -> 1.0, springDefault) |

### 4.6 What NOT to Animate

| Element | Reason |
|---------|--------|
| Sidebar navigation | Chrome should be instant and stable |
| Page headings | Static labels do not change state |
| Static text content | No re-entrance flicker on re-render |
| Already-visible cards | Cards only animate on *first* appearance, not on re-render |
| Scrollbar | Browser-native behaviour only |
| Focus indicators | Instant appearance for accessibility |

### 4.7 Reduced Motion

When `prefers-reduced-motion: reduce` is active:

- All spring animations become instant (duration: 0)
- Tween durations halve (200ms -> 100ms)
- Infinite animations (pulse, shimmer) are disabled
- Page transitions reduce to simple opacity fade (150ms)
- Card entrance stagger is removed (all cards appear simultaneously)

---

## 5. Accessibility

### 5.1 WCAG AA Contrast Matrix

All foreground/background combinations verified with `scripts/wcag_check.py`. Thresholds: normal text >= 4.5:1, large text >= 3.0:1.

| Foreground | Hex | Background | Hex | Ratio | Normal (4.5:1) | Large (3.0:1) |
|------------|-----|------------|-----|------:|:--------------:|:-------------:|
| `text-primary` | `#e2e8f0` | `bg-base` | `#0a0a12` | 15.99:1 | PASS | PASS |
| `text-primary` | `#e2e8f0` | `bg-surface` | `#0f0f1a` | 15.44:1 | PASS | PASS |
| `text-primary` | `#e2e8f0` | `bg-card` | `#13131f` | 14.93:1 | PASS | PASS |
| `text-primary` | `#e2e8f0` | `bg-card-hover` | `#181828` | 14.19:1 | PASS | PASS |
| `text-secondary` | `#94a3b8` | `bg-base` | `#0a0a12` | 7.69:1 | PASS | PASS |
| `text-secondary` | `#94a3b8` | `bg-surface` | `#0f0f1a` | 7.42:1 | PASS | PASS |
| `text-secondary` | `#94a3b8` | `bg-card` | `#13131f` | 7.18:1 | PASS | PASS |
| `text-secondary` | `#94a3b8` | `bg-card-hover` | `#181828` | 6.82:1 | PASS | PASS |
| `text-muted` | `#8b95a5` | `bg-base` | `#0a0a12` | 6.52:1 | PASS | PASS |
| `text-muted` | `#8b95a5` | `bg-surface` | `#0f0f1a` | 6.29:1 | PASS | PASS |
| `text-muted` | `#8b95a5` | `bg-card` | `#13131f` | 6.08:1 | PASS | PASS |
| `text-muted` | `#8b95a5` | `bg-card-hover` | `#181828` | 5.78:1 | PASS | PASS |
| `accent` | `#38bdf8` | `bg-base` | `#0a0a12` | 9.20:1 | PASS | PASS |
| `accent` | `#38bdf8` | `bg-surface` | `#0f0f1a` | 8.88:1 | PASS | PASS |
| `accent` | `#38bdf8` | `bg-card` | `#13131f` | 8.59:1 | PASS | PASS |
| `accent` | `#38bdf8` | `bg-card-hover` | `#181828` | 8.17:1 | PASS | PASS |
| `accent-dim` | `#0ea5e9` | `bg-base` | `#0a0a12` | 7.11:1 | PASS | PASS |
| `accent-dim` | `#0ea5e9` | `bg-surface` | `#0f0f1a` | 6.87:1 | PASS | PASS |
| `accent-dim` | `#0ea5e9` | `bg-card` | `#13131f` | 6.64:1 | PASS | PASS |
| `accent-dim` | `#0ea5e9` | `bg-card-hover` | `#181828` | 6.31:1 | PASS | PASS |
| `success` | `#10b981` | `bg-base` | `#0a0a12` | 7.77:1 | PASS | PASS |
| `success` | `#10b981` | `bg-surface` | `#0f0f1a` | 7.50:1 | PASS | PASS |
| `success` | `#10b981` | `bg-card` | `#13131f` | 7.26:1 | PASS | PASS |
| `success` | `#10b981` | `bg-card-hover` | `#181828` | 6.90:1 | PASS | PASS |
| `warning` | `#f59e0b` | `bg-base` | `#0a0a12` | 9.18:1 | PASS | PASS |
| `warning` | `#f59e0b` | `bg-surface` | `#0f0f1a` | 8.86:1 | PASS | PASS |
| `warning` | `#f59e0b` | `bg-card` | `#13131f` | 8.57:1 | PASS | PASS |
| `warning` | `#f59e0b` | `bg-card-hover` | `#181828` | 8.15:1 | PASS | PASS |
| `danger` | `#ef4444` | `bg-base` | `#0a0a12` | 5.24:1 | PASS | PASS |
| `danger` | `#ef4444` | `bg-surface` | `#0f0f1a` | 5.06:1 | PASS | PASS |
| `danger` | `#ef4444` | `bg-card` | `#13131f` | 4.89:1 | PASS | PASS |
| `danger` | `#ef4444` | `bg-card-hover` | `#181828` | 4.65:1 | PASS | PASS |

**Result**: All 32 foreground/background combinations pass WCAG AA for both normal and large text.

**Closest to threshold**: `danger` on `bg-card-hover` at 4.65:1 (threshold 4.5:1). This is safe but should not be used at font sizes smaller than 11px.

### 5.2 Focus Indicators

| Property | Value |
|----------|-------|
| Style | 2px solid ring |
| Offset | 2px (gap between element and ring) |
| Colour | `accent` (`#38bdf8`) |
| Visibility | Must be visible on all background colours (verified: accent on bg-base = 9.20:1) |
| `:focus-visible` | Show ring only on keyboard focus, not mouse click |

### 5.3 ARIA Requirements

| Pattern | ARIA implementation |
|---------|--------------------|
| Real-time data feeds | `aria-live="polite"` on metric values, task counts, agent status |
| Icon-only buttons | `aria-label` describing the action (e.g. "Close panel", "Expand sidebar") |
| Status dots | `aria-label` with status text (e.g. "Status: active"), never rely on colour alone |
| Modals/overlays | `role="dialog"`, `aria-modal="true"`, focus trap, Escape to close |
| Tab panels | `role="tablist"`, `role="tab"`, `role="tabpanel"`, arrow key navigation |
| Notifications | `aria-live="assertive"` for critical alerts, `"polite"` for informational |
| Drag-and-drop | `aria-roledescription="draggable item"` on draggable elements, `aria-live="assertive"` announcements for drag start/over/drop events |
| Command palette | `role="combobox"`, `aria-expanded`, `aria-activedescendant` for selection |

### 5.4 Status Encoding

Status must **never** be colour-only. Every status indicator includes:

1. **Colour**: semantic state colour (success/warning/danger/accent)
2. **Shape**: dot (6px circle) or icon (Lucide icon set)
3. **Text label**: explicit text for critical states ("Active", "Error", "Idle")

For non-critical contexts where space is limited (e.g. compact table rows), colour + shape is acceptable, but an `aria-label` must provide the text equivalent.

### 5.5 Touch and Click Targets

| Property | Value |
|----------|-------|
| Minimum target size | 32 x 32px (interactive area, not visual size) |
| Button minimum height | 32px |
| Icon button minimum | 32 x 32px clickable area (icon may be smaller visually) |
| Spacing between targets | Minimum 8px gap to prevent mis-taps |

---

## 6. Responsive Breakpoints

Scope inherited from [Page Structure & IA](page-structure.md). Desktop-first with minimal tablet support.

### Breakpoint Definitions

| Breakpoint | Range | Sidebar | Content layout | Tailwind class |
|------------|-------|---------|----------------|----------------|
| Desktop | >= 1280px | Full (220px expanded) | Multi-column grids | `xl:` |
| Desktop small | 1024 - 1279px | Auto-collapses to icon rail (56px) | Full width minus rail | `lg:` |
| Tablet | 768 - 1023px | Hidden (hamburger toggle, 240px overlay) | Single column | `md:` |
| Mobile | < 768px | Hidden | "Desktop Required" overlay (`MobileUnsupportedOverlay`) | default |

### Layout Adaptations

| Component | Desktop (>= 1280px) | Desktop small (1024-1279px) | Tablet (768-1023px) |
|-----------|---------------------|---------------------------|---------------------|
| Metric cards | 4-column grid | 4-column grid | 2-column grid |
| Section panels | 2-column grid | 2-column grid | Single column stack |
| Org chart | Full canvas | Full canvas | Horizontal scroll |
| Task board | Multi-column kanban | Multi-column kanban | Single column list fallback |
| Agent cards | 3-4 column grid | 3-column grid | 2-column grid |
| Data tables | Full columns | Horizontal scroll | Horizontal scroll |

### Sidebar Behaviour by Breakpoint

| Breakpoint | Default state | Toggle | Width |
|------------|---------------|--------|-------|
| >= 1280px | Expanded | Collapse to rail (56px) | 220px / 56px |
| 1024 - 1279px | Collapsed (rail) | Expand to full | 56px / 220px |
| 768 - 1023px | Hidden | Hamburger opens overlay | 0px / 240px (overlay) |
| < 768px | Hidden | No toggle (mobile not supported) | 0px |

Sidebar state is persisted in user preferences. When resizing from >= 1280px into the 1024-1279px range, the sidebar auto-collapses to the icon rail. The user's theme preference is not mutated; the effective mode is computed locally by combining the preference with the current breakpoint. At tablet (768-1023px), the sidebar renders as a 240px overlay via the shared `Drawer` component (`role="dialog"`, `aria-modal="true"`) with a blurred semi-transparent backdrop. It is triggered by a hamburger button (`Menu` icon) in the StatusBar, with `aria-expanded` tracking. The overlay closes on: backdrop click, X button, Escape key, or navigation (clicking a nav item). Below 768px, a `MobileUnsupportedOverlay` shows "Desktop Required" with a CLI hint (`synthorg status`).

---

## Exported Artifacts

### Tailwind `@theme` Snippet

The following `@theme` block contains all design tokens for Tailwind v4. This replaces the existing colour definitions in `web/src/styles/global.css` (to be integrated in #775).

> **Note**: The `@theme` block uses Tailwind's native property naming (`--color-*`, `--spacing-*`), while `design-tokens.css` uses the `--so-*` prefix for non-Tailwind contexts. Both define the same underlying values.

```css
@theme {
  /* Brand colors */
  --color-accent: #38bdf8;
  --color-accent-dim: #0ea5e9;

  /* State colors */
  --color-success: #10b981;
  --color-warning: #f59e0b;
  --color-danger: #ef4444;

  /* Text colors */
  --color-text-primary: #e2e8f0;
  --color-text-secondary: #94a3b8;
  --color-text-muted: #8b95a5;

  /* Background colors */
  --color-bg-base: #0a0a12;
  --color-bg-surface: #0f0f1a;
  --color-bg-card: #13131f;
  --color-bg-card-hover: #181828;

  /* Border colors */
  --color-border: #1e1e2e;
  --color-border-bright: #2a2a3e;

  /* Typography */
  --font-sans: 'Geist Variable', ui-sans-serif, system-ui, sans-serif;
  --font-mono: 'Geist Mono Variable', ui-monospace, monospace;

  /* Spacing (8px base) */
  --spacing-1: 4px;
  --spacing-2: 8px;
  --spacing-3: 12px;
  --spacing-4: 16px;
  --spacing-5: 20px;
  --spacing-6: 24px;
  --spacing-8: 32px;
  --spacing-10: 48px;
  --spacing-12: 64px;

  /* Radii */
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  --radius-xl: 12px;
}
```

### CSS Custom Properties

Exported to `web/src/styles/design-tokens.css` for non-Tailwind contexts (e.g. inline styles, third-party library theming, SVG styling).

### Motion Config

Exported to `web/src/lib/motion.ts` as TypeScript constants. Import and use:

```tsx
import { springDefault, tweenDefault, cardEntrance, staggerChildren } from "@/lib/motion";

<motion.div variants={cardEntrance} initial="hidden" animate="visible">
  ...
</motion.div>
```

---

## Reference Materials

| Resource | Location |
|----------|----------|
| Brand identity rationale | [Brand & UX](brand-and-ux.md) |
| Page structure and navigation | [Page Structure & IA](page-structure.md) |
| WCAG verification script | `scripts/wcag_check.py` |
| CSS design tokens | `web/src/styles/design-tokens.css` |
| Motion presets | `web/src/lib/motion.ts` |
| CSP nonce reader | `web/src/lib/csp.ts` |
| Structured logger factory | `web/src/lib/logger.ts` |
| Winning prototype (visual reference) | `research/762-ux-mockups` branch, `mockups/direction-cd/` |
| Design exploration mockups | `feat/765-design-exploration` branch, `mockups-v2/` |
| Design tokens implementation | #775 |
| Parent UX overhaul issue | #762 |

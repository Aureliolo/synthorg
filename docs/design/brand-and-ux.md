---
title: Brand Identity & UX Design System
description: Visual identity, theme architecture, colour system, typography, density, animation, and UI guidelines for the SynthOrg dashboard.
---

# Brand Identity & UX Design System

## Design Direction

**Chosen direction**: Warm Ops, a warm, approachable aesthetic with balanced density and spring-physics interactions, combined with semantic state-driven colour encoding where every colour communicates system status rather than serving as decoration.

**Why this direction**: Warmth makes an AI operations tool feel approachable without losing professionalism. Tying colour exclusively to state (green = rising, amber = attention, red = critical) gives operators instant comprehension of system health. The brand accent is a warm soft blue, deliberately neutral so that semantic state colours dominate the visual hierarchy. Orange/amber means "attention needed," not "this is SynthOrg."

**What was rejected and why**: A cool blue-cyan palette (data centre aesthetic) was too generic, indistinguishable from Grafana/Datadog screenshots. A neutral grey palette (no hue) lacked enough identity to be recognisable. A high-energy violet/purple palette was visually fatiguing for sustained 8-hour use. These directions scored well on individual criteria but failed the combination of identity distinctiveness + sustained usability.

**Design influences**: Linear (clean layout, balanced density), Vercel (status-first design), Dust.tt (warm approachability), Grafana (data density as a user preference).

## Colour System

### Semantic Colour Tokens

Colours are **state-driven**, not decorative. Every coloured element answers: "what is the system telling me?"

| Token | Purpose | Example hex | When to use |
|-------|---------|-------------|-------------|
| `accent` | Brand, interactive elements, links, focus rings, active nav | `#38bdf8` (warm soft blue, tunable) | Default state, clickable things, brand identity |
| `accent-dim` | Muted brand, secondary interactive, onboarding | Derived from accent | Hover states, secondary info, less prominent interactive |
| `success` | Rising, improving, healthy, completed | `#10b981` (emerald) | Metrics trending up, tasks completed, agents active |
| `warning` | Declining, degrading, attention needed | `#f59e0b` (amber) | Metrics trending down, budget nearing limit, stale tasks |
| `danger` | Critical, error, immediate action | `#ef4444` (red) | Agent errors, budget exceeded, failed tasks |
| `text-primary` | Main content text | `#e2e8f0` | Headings, values, primary content |
| `text-secondary` | Supporting text | `#94a3b8` | Labels, descriptions, secondary info |
| `text-muted` | Least prominent text | `#8b95a5` | Timestamps, metadata, disabled items |
| `bg-base` | Page background | `#0a0a12` | Deepest background layer |
| `bg-surface` | Sidebar, elevated surfaces | `#0f0f1a` | Sidebar, panels, raised areas |
| `bg-card` | Card backgrounds | `#13131f` | All card containers |
| `bg-card-hover` | Card hover state | `#181828` | Card background on mouse-over |
| `border` | Default borders | `#1e1e2e` | Card borders, dividers |
| `border-bright` | Interactive/hover borders | `#2a2a3e` | Focus rings, hover states |

### Dynamic Colour Assignment

Metric cards, sparklines, and trend indicators use colours dynamically based on direction of change:

| Data state | Colour token | Rationale |
|------------|-------------|-----------|
| Improving / rising | `success` | Green = things getting better |
| Stable / normal | `accent` or `text-muted` | Neutral; no action needed |
| Declining / degrading | `warning` | Amber = attention warranted |
| Critical / threshold | `danger` | Red = act now |

This ensures operators instantly understand system state from colours alone, without reading values. The same metric card shows green when tasks are completing faster and amber when they are slowing down.

### How to Add a New Colour Theme

Each theme is a single configuration object (~50 lines). All colours are CSS custom properties consumed via `var(--theme-*)` tokens. To add a new theme:

1. Create a new theme config (e.g. `themes/midnight.ts`) with all colour tokens
2. Register it in the theme index
3. Done; all components automatically pick up the new palette

No component code changes required. The 5 exploration themes (Ice Station, Warm Ops, Stealth, Signal, Neon) demonstrate this pattern: each theme is ~50 lines of colour token definitions with zero component changes.

## Typography

### Chosen Pairing

| Role | Font | Usage |
|------|------|-------|
| Monospace | Geist Mono (via @fontsource, self-hosted) | Data values, code, metrics, timestamps, agent names |
| Sans-serif | Geist Sans (via @fontsource, self-hosted) | Labels, descriptions, UI text, headings |

**Rationale**: Geist was designed by Vercel specifically for developer dashboards. Excellent readability at small sizes, clean number rendering in mono, professional but not clinical in sans.

**Typography is a theme axis**: other pairings (JetBrains Mono + Inter, IBM Plex Mono + IBM Plex Sans) are available and can be selected independently of colours.

### Self-Hosted Fonts

All fonts are bundled via `@fontsource` packages. No external CDN (Google Fonts) dependencies. This ensures:

- No privacy concerns from third-party font loading
- Consistent rendering regardless of network
- Faster first contentful paint (no font fetch waterfall)

## Density

Density is an **independent user preference**, not tied to theme colours.

| Level | Padding | Section gap | Grid gap | Metric size | Body size | Use case |
|-------|---------|-------------|----------|-------------|-----------|----------|
| Dense | `p-3` (12px) | `gap-3` (12px) | `gap-3` (12px) | `text-2xl` | `text-xs` | Power users, large monitors, data-heavy workflows |
| Balanced (default) | `p-4` (16px) | `gap-4` (16px) | `gap-4` (16px) | `text-3xl` | `text-sm` | General use, comfortable reading distance |
| Medium | `p-[14px]` | `gap-4` (16px) | `gap-4` (16px) | `text-2xl` | `text-xs` | Slightly tighter than balanced |
| Sparse | `p-5` (20px) | `gap-6` (24px) | `gap-6` (24px) | `text-3xl` | `text-sm` | Presentation mode, low information density tasks |

> **Token usage**: Components must use density-aware token classes (`p-card`, `space-y-section-gap`, `gap-section-gap`, `gap-grid-gap`) instead of the raw Tailwind utilities shown above. The raw values are what each token resolves to at each density level.

### How to Add a New Density Level

Create a new `ThemeDensity` object with padding, gap, and font size values. Register it in the density index. Components read density from the theme context; no component changes needed.

## Animation

Animation is an **independent user preference**, controlling motion intensity.

| Profile | Card entrance | Hover | Page transition | Status pulse | Use case |
|---------|---------------|-------|-----------------|--------------|----------|
| Minimal | 200ms fade | None | Fade only | Subtle | Reduced motion preference, distraction-free |
| Spring | Spring physics | Lift + shadow | Slide | Yes | Playful, responsive feel |
| Instant | No animation | None | None | No | Maximum performance, zero latency feel |
| Status-driven | Fade | None | Fade | Only on state change | Animation earns attention; only moving things changed |
| Aggressive | Slide + fade + scale | Lift + glow | Scale | Yes + shimmer | High energy, demo/presentation mode |

**Recommended default**: Status-driven. Animation should communicate state change, not decoration. Static elements stay still; only things that changed move.

## Sidebar

Sidebar mode is an **independent user preference**.

| Mode | Behaviour | Width | Best for |
|------|----------|-------|----------|
| Rail | Always visible, icon + label | 220px | Standard desktop use |
| Collapsible (default) | Expanded by default, can collapse to icon rail. Remembers user preference. | 220px / 56px | Most users (full nav when needed, compact when focused) |
| Hidden | Hamburger toggle, content gets full width | 240px (overlay) | Maximum content area, presentation |
| Persistent | Always expanded with notification badges | 220px | High-interactivity workflows, many nav items |
| Compact | Always visible, icons prominent, text secondary | 56px | Small screens, secondary monitors |

### Persistence

Sidebar collapse state is persisted in user preferences. If a user collapses the sidebar, it stays collapsed across sessions until they expand it again.

## Theme Architecture

### Independent Axes

The theme system has 5 orthogonal axes that users can configure independently:

```text
Color Palette  x  Density  x  Typography  x  Animation  x  Sidebar Mode
```

This gives users full control without combinatorial explosion in theme definitions. A user can run "warm blue colours + dense layout + IBM Plex fonts + minimal animation + compact sidebar" without any custom theme code.

### Implementation Pattern

```text
ThemeProvider (React context)
  |-- sets CSS custom properties on wrapper div (--theme-accent, --theme-bg-base, etc.)
  |-- Tailwind @theme block maps --theme-* to Tailwind utility classes
  |-- Components use Tailwind classes (text-accent, bg-bg-card, border-border)
  |-- Density/animation read from theme context object
  |-- Sidebar mode selects which sidebar component to render
```

### Critical Implementation Note: Tailwind v4 CSS Layers

When using Tailwind v4 with `@import "tailwindcss"`, **all custom CSS resets MUST be inside `@layer base`**. Tailwind v4 uses CSS cascade layers, and unlayered styles (like `* { margin: 0; padding: 0; }`) have higher priority than layered utilities, silently overriding all spacing, padding, margin, and gap utilities.

```css
/* WRONG -- breaks all Tailwind spacing utilities */
* { margin: 0; padding: 0; }

/* CORRECT -- respects Tailwind cascade layers */
@layer base {
  * { margin: 0; padding: 0; }
}
```

This was discovered during the design exploration (#765) and caused layout breakage that was difficult to diagnose because the utilities appeared in the generated CSS but had no visual effect.

## Dark Mode

**Dark mode only** (confirmed in #762 research). No light mode planned. All colour tokens assume dark backgrounds. WCAG AA contrast ratios are validated against dark card/surface backgrounds.

## Accessibility

- WCAG AA contrast minimum on all text (4.5:1 on backgrounds, 3:1 for large text)
- `prefers-reduced-motion` supported: `AnimatedPresence` uses `reducedPageVariants` (opacity-only fade), skeleton shimmer disabled via CSS media query, Motion's `useReducedMotion()` hook used for runtime detection
- Keyboard navigation for all interactive elements
- `aria-hidden="true"` on decorative icons
- Escape key closes overlays/drawers
- **Storybook a11y enforcement**: `parameters.a11y.test: 'error'` set globally in `.storybook/preview.tsx`; all stories fail on WCAG violations, catching regressions at component development time

## Storybook Tooling (v10)

The component development environment uses Storybook 10 with native type-safe configuration:

- **Config**: `defineMain` (from `@storybook/react-vite/node`) and `definePreview` (from `@storybook/react-vite`) for full TypeScript inference
- **Addons**: `@storybook/addon-docs` (autodocs) and `@storybook/addon-a11y` (WCAG testing). Essentials (backgrounds, controls, viewport, actions) and interactions are built into core
- **Backgrounds**: Selected via `initialGlobals.backgrounds.value = 'dark'`, which references our `--so-bg-base` token (`#0a0a12`) through `backgrounds.options.dark.value`, ensuring stories render against the actual brand dark background
- **Decorator**: Global dark-mode wrapper (`div.dark.bg-background.p-4.text-foreground`) applies our design tokens to all stories
- **API mocking**: MSW (Mock Service Worker) via `msw-storybook-addon`. Global `mswLoader` in `preview.tsx` intercepts API calls. Stories declare handlers via `parameters.msw.handlers` using pre-built handler arrays from `web/src/mocks/handlers/`. All responses use the `ApiResponse<T>` envelope via `apiSuccess()` helper

## Component Inventory

The following shared components live in `web/src/components/ui/` and form the building blocks for all dashboard pages. **Always compose pages from these**; never recreate equivalent functionality inline.

### Core Components

| Component | File | Props | Purpose |
|-----------|------|-------|---------|
| `StatusBadge` | `status-badge.tsx` | `status`, `label?`, `pulse?` | Status indicator dot. `label` is a boolean that toggles display of the built-in status text (not a custom string). Maps `AgentRuntimeStatus` to semantic colours via `getStatusColor()`. |
| `MetricCard` | `metric-card.tsx` | `label`, `value`, `change?`, `sparklineData?`, `progress?`, `subText?` | Numeric KPI display with optional sparkline, change badge (+/-%), and progress bar. |
| `Sparkline` | `sparkline.tsx` | `data`, `color?`, `width?`, `height?`, `animated?` | Pure SVG sparkline with gradient fill and animated draw. `color` defaults to `var(--so-accent)`. Standalone or inside MetricCard. |
| `SectionCard` | `section-card.tsx` | `title`, `icon?`, `action?`, `children` | Titled card wrapper with Lucide icon, action slot, and content area. Use for every content section. |
| `AgentCard` | `agent-card.tsx` | `name`, `role`, `department`, `status`, `currentTask?`, `timestamp?` | Consistent agent display. Composes Avatar + StatusBadge internally. Must look identical everywhere. |
| `DeptHealthBar` | `dept-health-bar.tsx` | `name`, `health?`, `agentCount` | Animated horizontal fill bar with utilisation percentage (null-safe; shows N/A when utilisation unavailable). Colour auto-mapped via `getHealthColor()`. |
| `ProgressGauge` | `progress-gauge.tsx` | `value`, `max?`, `label?`, `variant?`, `size?` | Circular or linear gauge for budget/utilisation. `variant` defaults to `'circular'`, `max` defaults to 100. |
| `StatPill` | `stat-pill.tsx` | `label`, `value` | Compact inline label + value pair for metadata rows. |
| `Avatar` | `avatar.tsx` | `name`, `size?`, `borderColor?` | Circular initials avatar with optional coloured border. Sizes: sm (24px), md (32px), lg (40px). |
| `Button` | `button.tsx` | shadcn standard | Standard button component (shadcn/ui). |
| `TaskStatusIndicator` | `task-status-indicator.tsx` | `status: TaskStatus`, `label?: boolean`, `pulse?: boolean`, `className?: string` | Task status dot with optional label and pulse animation. |
| `PriorityBadge` | `task-status-indicator.tsx` | `priority: Priority`, `className?: string` | Task priority coloured pill badge. |
| `ProviderHealthBadge` | `provider-health-badge.tsx` | `status: ProviderHealthStatus`, `label?: boolean`, `pulse?: boolean`, `className?: string` | Provider health status dot (up/degraded/down/unknown) with optional label. |

### Interaction Components

| Component | File | Props | Purpose |
|-----------|------|-------|---------|
| `Toast` / `ToastContainer` | `toast.tsx` | `toast` (ToastItem), `onDismiss`, `maxVisible?` | Notification toasts (success/error/warning/info) with auto-dismiss queue, Motion animations. Mount `ToastContainer` once in AppLayout. |
| `Skeleton` variants | `skeleton.tsx` | `shimmer?`, `lines?`, `rows?`, `columns?` | Loading placeholders: `Skeleton` (base), `SkeletonText`, `SkeletonCard`, `SkeletonMetric`, `SkeletonTable`. Shimmer respects `prefers-reduced-motion`. |
| `EmptyState` | `empty-state.tsx` | `icon?`, `title`, `description?`, `action?` | No-data / no-results placeholder with optional action button. |
| `ErrorBoundary` | `error-boundary.tsx` | `fallback?`, `onReset?`, `level?` | React error boundary with retry. Levels: `page` (full-height), `section` (card), `component` (inline). |
| `ConfirmDialog` | `confirm-dialog.tsx` | `open`, `onOpenChange`, `title`, `onConfirm`, `variant?`, `loading?` | Confirmation modal built on Base UI AlertDialog. Variants: `default`, `destructive`. |
| `CommandPalette` | `command-palette.tsx` | `className?` | Global Cmd+K search built with cmdk-base. Hosted inside a Base UI Dialog for focus trapping, fuzzy search, scope toggle, recent items. |
| `InlineEdit` | `inline-edit.tsx` | `value`, `onSave`, `validate?`, `type?`, `disabled?` | Click-to-edit with Enter/Escape, inline validation, optimistic save via `useFlash`. |
| `AnimatedPresence` | `animated-presence.tsx` | `routeKey`, `className?` | Page transition wrapper. Uses Motion AnimatePresence with reduced-motion fallback. |
| `StaggerGroup` / `StaggerItem` | `stagger-group.tsx` | `staggerDelay?`, `animate?`, `layoutId?`, `layout?` | Card entrance stagger container with configurable delay and layout animation support. |
| `Drawer` | `drawer.tsx` | `open`, `onClose`, `title?`, `ariaLabel?`, `side?`, `contentClassName?`, `children`, `className?` | Slide-in panel (left or right via `side`, default right) with overlay, spring animation, focus trap, and Escape-to-close. At least one of `title` or `ariaLabel` must be provided for accessible naming. Header omitted when `title` is absent. |
| `InputField` | `input-field.tsx` | `label`, `error?`, `hint?`, `multiline?`, `rows?`, `placeholder?`, `required?`, `disabled?`, `type?`, `value`, `onChange` | Labelled text input with inline error/hint display and optional textarea mode. Extends native input/textarea props. |
| `SelectField` | `select-field.tsx` | `label`, `options`, `value`, `onChange`, `error?`, `hint?`, `placeholder?`, `required?`, `disabled?`, `className?` | Labelled select dropdown with error/hint display and placeholder support. |
| `SliderField` | `slider-field.tsx` | `label`, `value`, `onChange`, `min`, `max`, `step?`, `formatValue?`, `disabled?`, `className?` | Labelled range slider with custom value formatter and aria-live value display. |
| `ToggleField` | `toggle-field.tsx` | `label`, `checked`, `onChange`, `description?`, `disabled?` | Labelled toggle switch (role="switch") with optional description text. |
| `TagInput` | `tag-input.tsx` | `label`, `value`, `onChange`, `placeholder?`, `disabled?`, `className?` | Chip-style multi-value input with add/remove, keyboard support (Enter to add, Backspace to remove), paste splitting. |
| `TokenUsageBar` | `token-usage-bar.tsx` | `data`, `segments?`, `max?`, `animated?`, `className?` | Segmented horizontal meter bar for token usage (multi-segment with auto-colours, `role="meter"`, animated transitions). |
| `CodeMirrorEditor` | `code-mirror-editor.tsx` | `value`, `onChange`, `language`, `readOnly?`, `aria-label?`, `className?` | CodeMirror 6 editor with JSON/YAML modes, design-token dark theme, line numbers, bracket matching, and `readOnly` support. |
| `SegmentedControl` | `segmented-control.tsx` | `label`, `options`, `value`, `onChange`, `disabled?`, `size?`, `className?` | Accessible radiogroup with keyboard navigation (arrow keys + wrapping), size variants (`sm`/`md`), generic `<T extends string>` typing. |
| `ThemeToggle` | `theme-toggle.tsx` | `className?` | Base UI Popover with 5-axis theme controls (colour, density, typography, animation, sidebar). Rendered in StatusBar for global access. |
| `LiveRegion` | `live-region.tsx` | `children`, `politeness?`, `debounceMs?`, `className?` | Debounced ARIA live region wrapper for real-time WS updates without overwhelming screen readers. Default: 500ms polite, 0ms assertive. |
| `MobileUnsupportedOverlay` | `mobile-unsupported.tsx` | (none, self-managing) | Full-screen overlay at <768px viewports directing users to desktop or CLI. Self-manages visibility via `useBreakpoint`. |
| `LazyCodeMirrorEditor` | `lazy-code-mirror-editor.tsx` | Same as `CodeMirrorEditor` | Suspense-wrapped lazy-loaded CodeMirrorEditor. Drop-in replacement that defers ~200KB+ CodeMirror bundle. |
| `MetadataGrid` | `metadata-grid.tsx` | `items`, `columns?`, `className?` | Key-value metadata grid for detail pages with configurable 2/3/4 columns and density-aware spacing. |
| `ProjectStatusBadge` | `project-status-badge.tsx` | `status`, `showLabel?`, `className?` | Project status dot with optional label and semantic colours (planning/active/on_hold/completed/cancelled). |
| `ContentTypeBadge` | `content-type-badge.tsx` | `contentType`, `className?` | MIME content type pill badge with semantic colours (JSON, PDF, Image, Text, Markdown, CSV, Binary). |

### Utility Functions

| Function | File | Purpose |
|----------|------|---------|
| `cn()` | `lib/utils.ts` | Tailwind class merging (clsx + twMerge). Use in every component. |
| `getStatusColor()` | `lib/utils.ts` | Maps `AgentRuntimeStatus` to `SemanticColor \| "text-secondary"` token name (`offline` maps to `"text-secondary"`). |
| `getHealthColor()` | `lib/utils.ts` | Maps 0-100 percentage to `SemanticColor` (>=75 success, >=50 accent, >=25 warning, <25 danger). |
| `getTaskStatusColor()` | `utils/tasks.ts` | Maps `TaskStatus` to `SemanticColor`. |
| `getTaskStatusLabel()` | `utils/tasks.ts` | Maps `TaskStatus` to display label. |
| `getPriorityColor()` | `utils/tasks.ts` | Maps `Priority` to `SemanticColor`. |
| `getPriorityLabel()` | `utils/tasks.ts` | Maps `Priority` to display label. |
| `getTaskTypeLabel()` | `utils/tasks.ts` | Maps `TaskType` to display label. |
| `getProviderHealthColor()` | `utils/providers.ts` | Maps `ProviderHealthStatus` to `SemanticColor \| "muted"`. |
| `formatLatency()` | `utils/providers.ts` | Formats milliseconds to human-readable string (e.g. "123ms", "1.5s"). |
| `formatErrorRate()` | `utils/providers.ts` | Formats error rate percentage with <0.1% handling. |
| `formatTokenCount()` | `utils/providers.ts` | Formats token count with K/M suffixes. |
| `formatCost()` | `utils/providers.ts` | Formats cost value using the runtime-configured currency (falls back to `DEFAULT_CURRENCY`); display-only, no FX conversion. |
| `toRuntimeStatus()` | `utils/agents.ts` | Maps API-layer `AgentStatus` (HR lifecycle) to `AgentRuntimeStatus` for UI components. |
| `getRiskLevelColor()` | `utils/approvals.ts` | Maps `ApprovalRiskLevel` to `SemanticColor \| "accent-dim"`. |
| `getRiskLevelLabel()` | `utils/approvals.ts` | Maps `ApprovalRiskLevel` to display label. |
| `getRiskLevelIcon()` | `utils/approvals.ts` | Maps `ApprovalRiskLevel` to `LucideIcon`. |
| `getApprovalStatusColor()` | `utils/approvals.ts` | Maps `ApprovalStatus` to `SemanticColor \| "text-secondary"`. |
| `getApprovalStatusLabel()` | `utils/approvals.ts` | Maps `ApprovalStatus` to display label. |
| `getUrgencyColor()` | `utils/approvals.ts` | Maps `UrgencyLevel` to `SemanticColor \| "text-secondary"`. |
| `formatUrgency()` | `utils/approvals.ts` | Formats `seconds_remaining` into human-readable countdown string. |
| `groupByRiskLevel()` | `utils/approvals.ts` | Groups approvals into `Map<ApprovalRiskLevel, ApprovalResponse[]>` sorted critical-to-low. |
| `filterApprovals()` | `utils/approvals.ts` | Client-side filtering by status, risk level, action type, and search text. |
| `RISK_LEVEL_ORDER` | `utils/approvals.ts` | Numeric ordering map for risk levels (critical=0 through low=3). |
| `DOT_COLOR_CLASSES` | `utils/approvals.ts` | Maps `SemanticColor \| "accent-dim"` to Tailwind background classes. |
| `URGENCY_BADGE_CLASSES` | `utils/approvals.ts` | Maps `SemanticColor \| "text-secondary"` to Tailwind badge classes. |
| `formatFileSize()` | `utils/format.ts` | Formats byte count to human-readable size string (e.g. "1.2 MB", "340 KB"). |

### Design System Hooks

| Hook | File | Purpose |
|------|------|---------|
| `useFlash()` | `hooks/useFlash.ts` | Real-time update flash effect. Returns `{ flashing, flashClassName, triggerFlash, flashStyle }`. Uses `STATUS_FLASH` timing constants. |
| `useStatusTransition()` | `hooks/useStatusTransition.ts` | Animate between agent status colours. Returns `{ displayColor, motionProps }` for spreading on `motion.div`. |
| `useCommandPalette()` | `hooks/useCommandPalette.ts` | Global command palette state. `registerCommands()` adds page-local commands (cleanup on unmount). `open()` / `close()` / `toggle()`. |
| `useAnimationPreset()` | `hooks/useAnimationPreset.ts` | Returns animation config (`spring`, `tween`, `staggerDelay`, `enableLayout`) based on the user's theme animation preference. Components use this instead of directly referencing `lib/motion.ts` constants. |
| `useCountAnimation()` | `hooks/useCountAnimation.ts` | Animated numeric value transitions for metric displays. Uses rAF with ease-out cubic and `prefers-reduced-motion` support. |
| `useAutoScroll()` | `hooks/useAutoScroll.ts` | Auto-scroll container to bottom on new content, pausing when user scrolls away. Returns `{ isAutoScrolling, scrollToBottom }`. |
| `useBreakpoint()` | `hooks/useBreakpoint.ts` | Reactive viewport breakpoint detection via matchMedia. Returns `{ breakpoint, isDesktop, isTablet, isMobile }`. |

### Types

| Type | File | Values |
|------|------|--------|
| `AgentRuntimeStatus` | `lib/utils.ts` | `"active"`, `"idle"`, `"error"`, `"offline"` |
| `SemanticColor` | `lib/utils.ts` | `"success"`, `"accent"`, `"warning"`, `"danger"` |
| `TaskStatus` | `api/types` | `"created"`, `"assigned"`, `"in_progress"`, `"in_review"`, `"completed"`, `"blocked"`, `"failed"`, `"interrupted"`, `"cancelled"` |
| `Priority` | `api/types` | `"critical"`, `"high"`, `"medium"`, `"low"` |
| `ProviderHealthStatus` | `api/types` | `"up"`, `"degraded"`, `"down"`, `"unknown"` |
| `ApprovalStatus` | `api/types` | `"pending"`, `"approved"`, `"rejected"`, `"expired"` |
| `ApprovalRiskLevel` | `api/types` | `"low"`, `"medium"`, `"high"`, `"critical"` |
| `UrgencyLevel` | `api/types` | `"critical"`, `"high"`, `"normal"`, `"no_expiry"` |
| `ApprovalPageFilters` | `utils/approvals` | Filter shape: `status?`, `riskLevel?`, `actionType?`, `search?` |
| `ProjectStatus` | `api/types` | `"planning"`, `"active"`, `"on_hold"`, `"completed"`, `"cancelled"` |
| `ArtifactType` | `api/types` | `"code"`, `"tests"`, `"documentation"` |

### When to Create a New Shared Component

Create a new component in `web/src/components/ui/` when:

1. The same UI pattern appears (or will appear) on **2+ pages**
2. It represents a **semantic concept** (not just a styled div)
3. It has **configurable behaviour** via props (variants, states, sizes)

Every new shared component must have:

- A `.stories.tsx` file with all states (default, hover, loading, error, empty)
- A TypeScript props interface
- Design token usage exclusively (no hardcoded colours/fonts/spacing)
- `cn()` for conditional class merging

### Enforcement

A PostToolUse hook (`scripts/check_web_design_system.py`) runs automatically on every Edit/Write to `web/src/` files. See CLAUDE.md "Web Dashboard Design System" section for the full rule set.

## Reference Materials

| Resource | Location |
|----------|----------|
| Design exploration mockups (5 variations) | `feat/765-design-exploration` branch, `mockups-v2/` (exploration artifacts, not production code) |
| Original winning prototype (C+D direction) | `research/762-ux-mockups` branch, `mockups/direction-cd/` |
| UX research document | `research/762-ux-mockups` branch, `docs/design/ux-research.md` |
| Page structure and information architecture | [Page Structure & IA](page-structure.md) |
| UX design guidelines (implementation specs) | [UX Guidelines](ux-guidelines.md) |
| Parent issue (full UX overhaul) | #762 |
| Design exploration issue | #765 |
| Page structure issue | #766 |
| Design tokens implementation | #775 |
| UX guidelines document | #767 |

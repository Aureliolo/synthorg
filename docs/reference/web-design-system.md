---
title: Web Dashboard Design System
description: Component inventory, design token rules, and Base UI integration recipes for the React 19 dashboard at web/src/.
---

# Web Dashboard Design System

On-demand reference for `web/src/`. The short rules in `web/CLAUDE.md` are: reuse existing UI primitives before creating new ones, use design tokens for everything (no hardcoded hex / fonts / pixels / Motion durations), and route all locale-aware formatting through the `@/utils/format` helpers. This page is the component catalog and token recipe book; enforcement runs in `scripts/check_web_design_system.py` (PostToolUse hook on every `web/src/` edit).

## Design Token Rules

- **Colors**: use Tailwind semantic classes (`text-foreground`, `bg-card`, `text-accent`, `text-success`, `bg-danger`, etc.) or CSS variables (`var(--so-accent)`). NEVER hardcode hex values in `.tsx` / `.ts` files.
- **Typography**: use `font-sans` or `font-mono` (maps to Geist tokens). NEVER set `fontFamily` directly. For in-chart text size, use `var(--so-text-micro)`, `var(--so-text-compact)`, `var(--so-text-body-sm)`.
- **Spacing**: use density-aware tokens (`p-card`, `gap-section-gap`, `gap-grid-gap`) or standard Tailwind spacing. NEVER hardcode pixel values for layout spacing.
- **Shadows / Borders**: use token variables (`var(--so-shadow-card-hover)`, `border-border`, `border-bright`).
- **Responsive widths**: use the named-variant tokens for drawers, search inputs, and popovers. Drawers pick a width via `<Drawer width="narrow|default|wide">` (mapped to `--so-drawer-width-narrow|default|wide`); search inputs cap via `<SearchInput maxWidth="narrow|wide">` (mapped to `--so-search-max-narrow|wide`); popovers pass one of the two popover caps directly, e.g. `style={{ maxWidth: 'var(--so-popover-max-compact)' }}` for small menus or `style={{ maxWidth: 'var(--so-popover-max-wide)' }}` for larger surfaces. The token values use `clamp()` so they adapt to viewport and density automatically; NEVER add new inline `w-[40vw]` or `max-w-lg` drawer overrides.
- **Chart SVG attributes** (Recharts, xyflow, `<svg>`): use `var(--so-stroke-hairline)` (1) or `var(--so-stroke-thin)` (1.5) for `strokeWidth`; `var(--so-chart-fill-opacity-strong)` (0.3) or `var(--so-chart-fill-opacity-subtle)` (0.15) for `stopOpacity`; `var(--so-dash-tight)` / `--so-dash-compact` / `--so-dash-medium` / `--so-dash-loose` / `--so-dash-wide` for `strokeDasharray`. Modern browsers resolve CSS variables inside SVG presentation attributes. **Exception**: xyflow's `MiniMap` props (`maskStrokeWidth`, `nodeStrokeWidth`, `nodeBorderRadius`) and Recharts' `margin` prop are typed as `number` and reject CSS vars; use numeric constants and a comment pointing to the token.
- **Currency**: NEVER hardcode currency codes (`'EUR'`, `'USD'`) or symbols (`€`, `$`) in formatter calls. Import `DEFAULT_CURRENCY` from `@/utils/currencies` and pass it to `formatCurrency(value, DEFAULT_CURRENCY)` or read the runtime currency from the company / settings store.
- **Locale / i18n**: NEVER hardcode BCP 47 locale strings (`'en-US'`, `'fr-FR'`) or call bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()`. Use the helpers in `@/utils/format` (`formatDateTime`, `formatDateOnly`, `formatTime`, `formatDayLabel`, `formatTodayLabel`, `formatRelativeTime`, `formatNumber`, `formatCurrency`, `formatCurrencyCompact`, `formatTokenCount`), all of which accept an optional `locale?: string` that defaults to `getLocale()` from `@/utils/locale`. The locale source of truth is `APP_LOCALE` in `@/utils/locale`; swap in a settings-store read there when we add a user-facing locale toggle.

## Component Inventory

Every shared building block in `web/src/components/ui/`. Reuse before creating new components. The table groups primitives by purpose.

### Status, badges, indicators

| Component | Import | Use for |
|-----------|--------|---------|
| `StatusBadge` | `@/components/ui/status-badge` | Agent / task / system status indicators (colored dot + optional built-in `label`). Default emits `role="img"` with an aria-label. Pass `decorative` when the badge is visually labeled by adjacent text (emits `aria-hidden`); pass `announce` for live WS updates (emits `role="status"` + `aria-live="polite"`). |
| `TaskStatusIndicator` | `@/components/ui/task-status-indicator` | Task status dot with optional label and pulse animation (accepts `TaskStatus`). |
| `PriorityBadge` | `@/components/ui/task-status-indicator` | Task priority colored pill badge (critical / high / medium / low). |
| `ProviderHealthBadge` | `@/components/ui/provider-health-badge` | Provider health status indicator (up / degraded / down / unknown colored dot + optional label). |
| `ConnectionHealthBadge` | `@/components/ui/connection-health-badge` | Integration connection health (healthy / degraded / unhealthy / unknown); thin wrapper over `ProviderHealthBadge` that owns the enum mapping. |
| `ProjectStatusBadge` | `@/components/ui/project-status-badge` | Project status dot with optional label (planning / active / on_hold / completed / cancelled, semantic colors). |
| `ContentTypeBadge` | `@/components/ui/content-type-badge` | MIME content type pill badge with semantic colors (JSON, PDF, Image, Text, etc.). |
| `PolicySourceBadge` | `@/components/ui/policy-source-badge` | Ceremony policy field source indicator (project / department / default origin pill). |
| `StatPill` | `@/components/ui/stat-pill` | Compact inline label + value pair. |
| `Avatar` | `@/components/ui/avatar` | Circular initials avatar with optional `borderColor?` prop. |

### Cards, metrics, data visualization

| Component | Import | Use for |
|-----------|--------|---------|
| `MetricCard` | `@/components/ui/metric-card` | Numeric KPIs with sparkline, change badge, progress bar. |
| `Sparkline` | `@/components/ui/sparkline` | Inline SVG trend lines with `color?` and `animated?` props (used inside `MetricCard` or standalone). |
| `SectionCard` | `@/components/ui/section-card` | Titled card wrapper with icon and action slot. |
| `AgentCard` | `@/components/ui/agent-card` | Agent display: avatar, name, role, status, current task. |
| `DeptHealthBar` | `@/components/ui/dept-health-bar` | Department utilization: animated fill bar + `health?` (optional, shows N/A when null) + `agentCount` (required). |
| `ProgressGauge` | `@/components/ui/progress-gauge` | Circular or linear gauge for budget / utilization (`variant?` defaults to `'circular'`, `max?` defaults to 100). |
| `TokenUsageBar` | `@/components/ui/token-usage-bar` | Segmented horizontal meter bar for token usage (multi-segment with auto-colors, `role="meter"`, animated transitions). |
| `MetadataGrid` | `@/components/ui/metadata-grid` | Key-value metadata grid for detail pages with configurable columns (2 / 3 / 4), density-aware spacing. |

### Forms, inputs, controls

| Component | Import | Use for |
|-----------|--------|---------|
| `Button` | `@/components/ui/button` | Standard button (shadcn). |
| `InputField` | `@/components/ui/input-field` | Labeled text input with error / hint display, optional multiline textarea mode, optional `leadingIcon` (decorative, pointer-events-none) / `trailingElement` (interactive slot, e.g. clear button) positioned relative to the input box, not the labeled wrapper. When `type="password"`, automatically renders a built-in eye / eye-off visibility toggle as the trailing element; opt out via `hidePasswordToggle?: boolean`, or override entirely by supplying your own `trailingElement` (which takes precedence). |
| `PasswordVisibilityGroup` | `@/components/ui/input-field` | Context provider co-located with `InputField`. Wraps semantically-paired password / secret fields (e.g. Password + Confirm Password on the Create Admin Account screen) so a single eye toggle reveals or hides every field in the group at once. Independent secrets in the same dialog (e.g. an OAuth client secret next to a header value) should stay outside the provider so each toggles on its own. |
| `SelectField` | `@/components/ui/select-field` | Labeled select dropdown with error / hint and placeholder support. |
| `SliderField` | `@/components/ui/slider-field` | Labeled range slider with custom value formatter and aria-live display. |
| `ToggleField` | `@/components/ui/toggle-field` | Labeled toggle switch (`role="switch"`) with optional description text. |
| `InlineEdit` | `@/components/ui/inline-edit` | Click-to-edit text with Enter / Escape, validation, optimistic save with rollback. |
| `TagInput` | `@/components/ui/tag-input` | Chip-style multi-value input with add / remove, keyboard support (Enter to add, Backspace to remove), paste splitting. |
| `SegmentedControl` | `@/components/ui/segmented-control` | Accessible radiogroup with keyboard navigation, size variants (`sm` / `md`), generic `<T extends string>` typing. |
| `SearchInput` | `@/components/ui/search-input` | Search box with optional `/` global focus shortcut (`focusShortcut`), clear button, imperative `focus()` / `clear()` via React 19 `ref` prop. Named width cap via `maxWidth?: 'narrow' \| 'wide'` (default `'wide'`, mapped to `--so-search-max-*` tokens). |
| `InheritToggle` | `@/components/ui/inherit-toggle` | Toggle for inheriting vs. overriding a policy field from the parent level. |
| `CodeMirrorEditor` | `@/components/ui/code-mirror-editor` | CodeMirror 6 editor with JSON / YAML modes, design-token dark theme, line numbers, bracket matching, `readOnly` support. |
| `LazyCodeMirrorEditor` | `@/components/ui/lazy-code-mirror-editor` | Suspense-wrapped lazy-loaded `CodeMirrorEditor` (drop-in replacement, defers ~200KB+ CodeMirror bundle). |

### Layout, navigation, list-page primitives

| Component | Import | Use for |
|-----------|--------|---------|
| `Drawer` | `@/components/ui/drawer` | Slide-in panel (Base UI Drawer, `side`: left or right, default right) with overlay, CSS transitions, focus management + swipe-to-dismiss via Base UI, Escape-to-close, optional header (`title`), `ariaLabel` for accessible name (one of `title` or `ariaLabel` required), named width variant via `width?: 'compact' \| 'narrow' \| 'default' \| 'wide'` (default `'default'`, mapped to `--so-drawer-width-*` tokens; `compact` is the tightest variant tuned for content-light flows like single-field edits), and `contentClassName` override. |
| `Breadcrumbs` | `@/components/ui/breadcrumbs` | Breadcrumb navigation for deep detail pages. `<nav aria-label="Breadcrumb">` + `<ol>` + `aria-current="page"` on terminal item. React Router 7 `<Link>` for ancestors. Collapses middle items with ellipsis when exceeding `maxItems` (default 4). |
| `Pagination` | `@/components/ui/pagination` | List-page pagination control (client-side slice now; props-compatible for OPS-1 cursor mode). Keyboard shortcuts: Home / End / ArrowLeft / ArrowRight / PageUp / PageDown. `total=undefined` signals cursor mode (Next enabled, Last disabled). Pair with `useListPagination` hook for URL-persisted state. |
| `ListHeader` | `@/components/ui/list-header` | Standardised list-page header: title + count (formatted via `formatNumber`) on the left, `primaryAction` on the right, optional `secondaryActions` wrap below. Replaces ad-hoc `<div>` headers on list pages. |
| `SearchFilterSort` | `@/components/ui/search-filter-sort` | Layout wrapper for list-page controls (search + filters + sort). Named `search`, `filters`, `sort`, `trailing` slots. Pairs with `SearchInput` for the search slot. |
| `BulkActionBar` | `@/components/ui/bulk-action-bar` | Sticky bottom action bar for multi-select list pages. `selectedCount` renders "N selected" on the left, `children` slot takes caller-supplied action buttons (e.g. destructive `Delete N`), `onClear` wires the built-in Clear button. Motion-animated entrance / exit via the approvals-originated spring preset. Used by Workflows and Projects bulk-delete flows; pair with row-level `selected` / `onToggleSelect` props on your grid / table view. |
| `ThemeToggle` | `@/components/ui/theme-toggle` | Base UI Popover with 5-axis theme controls (color, density, typography, animation, sidebar), rendered in StatusBar. |
| `MobileUnsupportedOverlay` | `@/components/ui/mobile-unsupported` | Full-screen overlay at `<768px` viewports directing users to desktop or CLI; self-manages visibility via `useBreakpoint`. |

### Feedback, loading, and error states

| Component | Import | Use for |
|-----------|--------|---------|
| `Toast` / `ToastContainer` | `@/components/ui/toast` | Success / error / warning / info notifications with auto-dismiss queue (mount `ToastContainer` once in AppLayout). Store exposes `dismissAll()` (timers + toasts) and `cancelAllPending()` (timers only, preserves toasts) for test teardown; the global `afterEach` in `web/src/test-setup.tsx` uses `dismissAll()`. |
| `Skeleton` / `SkeletonCard` / `SkeletonMetric` / `SkeletonTable` / `SkeletonText` | `@/components/ui/skeleton` | Loading placeholders matching component shapes (shimmer animation, respects `prefers-reduced-motion`). |
| `EmptyState` | `@/components/ui/empty-state` | No-data / no-results placeholder with icon, title, description, optional action button. |
| `ErrorBoundary` | `@/components/ui/error-boundary` | React error boundary with retry. `level` prop: `page` / `section` / `component`. |
| `ErrorBanner` | `@/components/ui/error-banner` | Error / warning / info banner for list-fetch failures, offline state, onboarding retry guidance. `severity` maps to `role=alert` (error) or `role=status` (warning / info). `variant='offline'` forces warning + WifiOff icon. Optional `onRetry`, `retryAfterSeconds` (live "Retry in Ns" countdown that disables the Retry button until the cooldown expires; pair with `ApiRequestError.retryAfter` when surfacing 429 / 503 responses), `onDismiss`, `action` slots. Use this for every page-level or form-level error surface; use toasts for mutation outcomes instead. |
| `WsConnectionBanner` | `@/components/ui/ws-connection-banner` | Page-level offline notice for surfaces that depend on live WebSocket updates. Renders nothing while connected. The offline banner is suppressed only during a brief initial-handshake grace window (the WS store boots at `connected: false`); after that window elapses, or after the first successful connection, an offline state always surfaces, including a session that starts offline and never connects. Drop in at the top of any page that drives state from `useWebSocketStore`. |
| `ConfirmDialog` | `@/components/ui/confirm-dialog` | Confirmation modal (Base UI AlertDialog) with `default` / `destructive` variants and `loading` state. |
| `ProgressIndicator` | `@/components/ui/progress-indicator` | Long-running operation progress. Variants: `determinate` (labeled bar + percentage), `indeterminate` (shimmer), `stages` (multi-step list with done / running / pending / failed). Use for fine-tuning pipelines, setup flows, provider probes. |

### Animation primitives

| Component | Import | Use for |
|-----------|--------|---------|
| `AnimatedPresence` | `@/components/ui/animated-presence` | Page transition wrapper (Motion AnimatePresence keyed by route). |
| `StaggerGroup` / `StaggerItem` | `@/components/ui/stagger-group` | Card entrance stagger container with configurable delay. |
| `LiveRegion` | `@/components/ui/live-region` | Debounced ARIA live region wrapper (`polite` / `assertive`) for real-time WS updates without overwhelming screen readers. |

### Command palette and shortcuts

| Component | Import | Use for |
|-----------|--------|---------|
| `CommandPalette` | `@/components/ui/command-palette` | Global Cmd+K search (cmdk-base + Base UI Dialog + React Router). Mount once in AppLayout, register commands via `useCommandPalette` hook. |
| `KeyboardShortcutHint` | `@/components/ui/keyboard-shortcut-hint` | Inline `<kbd>` pills for tooltip / button hints. Example: `<KeyboardShortcutHint keys={['Ctrl', 'K']} label="palette" />`. |
| `CommandCheatsheet` | `@/components/ui/command-cheatsheet` | Full-screen overlay triggered by `?` showing all registered shortcuts grouped by section. Reads from `useShortcutRegistry`. Mount once in AppLayout. |

### Version rollback (cross-domain)

| Component | Import | Use for |
|-----------|--------|---------|
| `VersionTimeline` | `@/components/version-rollback/VersionTimeline` | Generic read-only timeline of `{ id, version, created_at }` snapshots. Domain-agnostic; reused across agent identity, role, budget config, evaluation config, and company version-rollback surfaces. Skeleton + empty + load-more states built in. |
| `VersionDiffDrawer` | `@/components/version-rollback/VersionDiffDrawer` | Side-by-side JSON diff of two versions in a Drawer (`width="wide"`). Pairs with `VersionTimeline` selection state. |
| `RollbackConfirmDialog` | `@/components/version-rollback/RollbackConfirmDialog` | Destructive `ConfirmDialog` wrapper with rollback-specific copy + a final preview of "what will change". |

### Setup-only

| Component | Import | Use for |
|-----------|--------|---------|
| `PostSetupGuidanceCard` | `@/components/setup/PostSetupGuidanceCard` | One-time guidance banner shown on the dashboard after setup completes. Visibility flag in localStorage under `synthorg.firstRun`; dismissible across reloads. |

### Provider picker (shared between wizard and Settings)

| Component | Import | Use for |
|-----------|--------|---------|
| `PresetPickerSections` | `@/components/providers/PresetPickerSections` | Canonical three-section provider picker (Cloud / Detected / Manual). Reused on the setup wizard's Providers step (`web/src/pages/setup/ProvidersStep.tsx`) and the Settings -> Providers page (`web/src/pages/ProvidersPage.tsx`). Owns no fetching: all state and callbacks come in via props. |
| `CloudProviderGrid` | `@/components/providers/CloudProviderGrid` | Logo-and-name grid for cloud presets. Click a card to open the credential form pre-filled with that preset; already-configured presets render disabled with a "Configured" tag. |
| `DetectedLocalList` | `@/components/providers/DetectedLocalList` | "Detected on this machine" panel for local LLM servers. Hidden entirely when probing is idle and nothing was found (no banner, no X marks). Each row offers `[Add local]` and, when a cloud counterpart exists in `LOCAL_TO_CLOUD_COUNTERPART` (currently Ollama -> Ollama Cloud), `[Add cloud]`. |
| `CustomConfigButton` | `@/components/providers/CustomConfigButton` | "Configure manually" entry point opening the credential form in custom-endpoint mode. |
| `ProviderLogo` | `@/components/providers/ProviderLogo` | Brand logo via `mask-image` against `bg-text-secondary` so the colour adapts cleanly to the active theme. SVGs live in `web/public/provider-logos/` (sourced from [lobe-icons](https://github.com/lobehub/lobe-icons), MIT). Falls back to a Lucide `Server` icon when the preset name is not in `KNOWN_LOGOS`. |

## Creating New Components

When a new shared component is needed (not covered by the inventory above):

1. Place it in `web/src/components/ui/` with a descriptive kebab-case filename.
2. Create a `.stories.tsx` file alongside it with all states (default, hover, loading, error, empty).
3. Export props as a TypeScript interface.
4. Use design tokens exclusively; no hardcoded colors, fonts, or spacing.
5. Import `cn` from `@/lib/utils` for conditional class merging.
6. **For primitives backed by Base UI** (Dialog, AlertDialog, Popover, Menu, Tabs, Drawer; see `web-base-ui-decisions.md` for the canonical list; `Select`, `Toast`, `Meter`, `Combobox`, `Tooltip` are intentionally **not** adopted):
   - Import from the specific subpath: `import { Dialog } from '@base-ui/react/dialog'`
   - Use the component's `render` prop for polymorphism: `<Dialog.Trigger render={<Button>Open</Button>} />`. Never spread props manually.
   - For Dialog / AlertDialog / Popover / Drawer: compose with `Portal` + `Backdrop` + `Popup`. Popover and Menu additionally require a `Positioner` wrapper that owns `side` / `align` / `sideOffset`. Drawer additionally supports `swipeDirection` on `Root` and `SwipeArea` for swipe-to-dismiss.
   - Animation state attributes are `data-[open]`, `data-[closed]`, `data-[starting-style]`, `data-[ending-style]` (not `data-[state=open]` / `data-[state=closed]`). Tabs Tab uses `data-[active]` (not `data-[state=active]`).
   - In Tailwind v4, `translate-*` and `scale-*` compile to the dedicated CSS `translate:` and `scale:` properties, not `transform:`. Transition property lists must name each one explicitly: `transition-[opacity,translate]` or `transition-[opacity,scale]`, not just `transition-[opacity,transform]`.
   - The local `<Slot>` helper in `components/ui/slot.tsx` is reserved for `<Button asChild>`; all other polymorphism goes through Base UI's `render` prop.

## What NOT to Do

- **Do NOT** recreate status dots inline; use `<StatusBadge>`.
- **Do NOT** build card-with-header layouts from scratch; use `<SectionCard>`.
- **Do NOT** create metric displays with `text-metric font-bold`; use `<MetricCard>`.
- **Do NOT** render initials circles manually; use `<Avatar>`.
- **Do NOT** create complex (more than 8 lines) JSX inside `.map()`; extract to a shared component.
- **Do NOT** use `rgba()` with hardcoded values; use design token variables.

## Enforcement

A PostToolUse hook (`scripts/check_web_design_system.py`) runs automatically on every Edit / Write to `web/src/` files. It catches:

- Hardcoded hex colors and rgba values.
- Hardcoded font-family declarations.
- Hardcoded Motion transition durations (should use `@/lib/motion` presets).
- Hardcoded BCP 47 locale literals (`'en-US'`, `'de-DE'`, etc.) in files that use `Intl.*` or `.toLocale*String(...)`; use helpers from `@/utils/format` instead.
- Bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()` calls without an explicit locale.
- New components without Storybook stories.
- Duplicate patterns that should use existing shared components.
- Complex `.map()` blocks that should be extracted.

Fix all violations before proceeding; do not suppress or ignore hook output.

## See also

- [regional-defaults.md](regional-defaults.md): currency / locale / timezone resolution.
- [web-base-ui-decisions.md](web-base-ui-decisions.md): adopted-vs-rejected Base UI primitives.
- [web-zustand-stores.md](web-zustand-stores.md): mutation pattern, MSW handlers, active-handle gate, WS protocol.
- [web-package-structure.md](web-package-structure.md): `web/src/` and `web/e2e/` directory layout.
- [web-post-training.md](web-post-training.md): TypeScript 6 / Storybook 10 facts that post-date the model's training cutoff.

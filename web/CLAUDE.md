# Web Dashboard

React 19 + shadcn/ui + Base UI + Tailwind CSS 4 + Motion + Zustand

`App.tsx` wraps the app in `<CSPProvider nonce={getCspNonce()}>` + `<MotionConfig nonce>` so every inline `<style>` tag injected by Base UI and Motion carries the per-request CSP nonce. See `docs/security.md` → CSP Nonce Infrastructure for the full flow. Base UI's `render` prop is the polymorphism primitive used throughout the dashboard; the local `<Slot>` helper in `components/ui/slot.tsx` uses `@base-ui/react/merge-props` to support the `<Button asChild>` ergonomic (the only component that uses this helper; all other primitives use Base UI's native `render` prop directly).

## Quick Commands

```bash
npm --prefix web install                   # install frontend deps
npm --prefix web run dev                   # dev server (http://localhost:5173)
npm --prefix web run build                 # production build
npm --prefix web run lint                  # ESLint (zero warnings enforced)
npm --prefix web run type-check            # TypeScript type checking
npm --prefix web run test                  # Vitest unit tests (coverage scoped to files changed vs origin/main)
npm --prefix web run test -- --coverage --detect-async-leaks  # Full suite + unhandled-handle detection (matches CI)
npm --prefix web run analyze               # bundle size treemap (opens stats.html)
npm --prefix web run e2e                   # Playwright visual regression tests
npm --prefix web run e2e:update            # update Playwright screenshot baselines
npm --prefix web run lighthouse            # Lighthouse performance audit (target: 90+)
npm --prefix web run storybook             # Storybook dev server (http://localhost:6006)
npm --prefix web run storybook:build       # Storybook production build
```

## Package Structure

```text
web/src/
  api/            # Axios client (`client.ts`), endpoint modules (`endpoints/`, 38 domains), and narrow-domain types under `types/` (27 files, no barrel `index.ts` -- consumers import directly from `@/api/types/<domain>`)
  components/     # React components: ui/ (shadcn primitives + SynthOrg core components), layout/ (app shell, sidebar with external link support, status bar); feature dirs added as pages are built
  hooks/          # React hooks (auth, login lockout, WebSocket, polling, optimistic updates, command palette, flash effects, status transitions, page data composition, count animation, auto-scroll, roving tabindex, breakpoint detection, update tracking, animation presets, settings dirty state, settings keyboard shortcuts, communication edges, artifact/project data composition, useWorkflowsData)
  lib/            # Utilities (cn() class merging, semantic color mappers), Motion presets, CSP nonce reader, structured logger factory
  mocks/          # MSW request handlers (handlers/) shared between Storybook stories and the Vitest suite; test-setup.tsx bootstraps them via setupServer(...defaultHandlers)
  pages/          # Lazy-loaded page components (one per route); page-scoped sub-components in pages/<page-name>/ subdirs (e.g. tasks/, org-edit/, settings/, workflows/, fine-tuning/, training/)
  router/         # React Router config, route constants (incl. DOCUMENTATION -- external, not SPA-routed), auth/setup guards
  stores/         # Zustand stores (auth, WebSocket, toast, analytics, company, agents, approvals, budget, meetings, messages, tasks, settings, sinks, artifacts, projects, theme, workflows, fine-tuning, ceremony-policy, setup, training, per-domain stores). Stores over ~600 lines are sliced into packages. Two aggregation patterns are used: (1) package-internal index -- `setup-wizard/` (navigation, template, company, providers, agents, theme, completion) and `workflow-editor/` (graph, undo-redo, validation, clipboard, persistence, versions, yaml) both expose a composed `index.ts` and consumers import from `@/stores/setup-wizard` / `@/stores/workflow-editor`; (2) sibling aggregator module -- `providers/` (crud-actions, list-actions, local-model-actions), `connections/` (crud-actions, list-actions), `mcp-catalog/` (list-actions, install-actions) each live next to a top-level `providers.ts` / `connections.ts` / `mcp-catalog.ts` which composes the slices; consumers import from `@/stores/providers` etc. which resolves to the `.ts` aggregator. Each package has a `types.ts` regardless of pattern.
  styles/         # Design tokens (--so-* CSS custom properties, single source of truth) and Tailwind theme bridge
  utils/          # Constants, error handling, formatting, logging
  __tests__/      # Vitest unit + property tests (mirrors src/ structure)
```

## Logging

- **Always** use `createLogger` from `@/lib/logger`; never bare `console.warn`/`console.error`/`console.debug` in application code
- **Variable name**: always `log` (e.g. `const log = createLogger('module-name')`)
- **Only `logger.ts` itself** may use bare console methods
- **Levels**: `log.debug()` (DEV-only, stripped in production), `log.warn()`, `log.error()`
- **Static messages**: pass dynamic/untrusted values as separate args (not interpolated into the message string) so they go through `sanitizeArg`
- **Attacker-controlled fields** inside structured objects must be wrapped in `sanitizeForLog()` before embedding

## Zustand Store Error Handling (MANDATORY)

All Zustand store **mutation** actions (create/update/delete) MUST follow the `stores/connections/crud-actions.ts` pattern:

1. Wrap the API call in `try`/`catch`.
2. On success: update the store + emit a success toast via `useToastStore.getState().add({ variant: 'success', title: '...' })`.
3. On failure: log via `log.error('...', sanitizeForLog(err))`, emit an error toast with `description: getErrorMessage(err)`, and **return a sentinel** (`null` for create/update returning an entity, `false` for delete returning a boolean).
4. For optimistic mutations, capture `previous` state synchronously and restore it in the `catch` branch.

**Callers MUST NOT wrap store mutation calls in `try`/`catch`**; the store owns the error UX. Callers only need to null-check the sentinel to decide whether to navigate, dismiss a dialog, or run a rollback.

**List reads** (`fetch*`) follow the same pattern for logging but set `error: string | null` on the store instead of toasting; the UI surface (usually a page-level error banner) consumes the error state.

**Cursor pagination (MANDATORY)**: list endpoints use opaque cursor-based paging. The wire envelope is `PaginationMeta { limit, next_cursor: string | null, has_more: boolean, total: number | null, offset: number }`, unwrapped in `@/api/client` to `PaginatedResult<T> { data, total, offset, limit, nextCursor, hasMore, pagination }`. Stores that expose `fetchMore*` actions keep `nextCursor` + `hasMore` in state (not offset arithmetic) and early-return when `!hasMore || !nextCursor`; call sites pass `{ cursor: state.nextCursor, limit }` to the endpoint. `total` is nullable: repo-backed endpoints omit the extra `COUNT(*)` round-trip, so derive display counts from `data.length` when `total === null`. `has_more=true` with `next_cursor=null` is rejected server-side by `PaginationMeta._validate_cursor_consistency`; the two fields always agree.

**Health / readiness endpoints (MANDATORY)**: the dashboard calls `getLiveness()` (`/api/v1/healthz`, always 200 while the process is alive) and `getReadiness()` (`/api/v1/readyz`, 200 on healthy persistence + message bus, 503 otherwise). `ReadinessOutcome` is a binary `'ok' | 'unavailable'` union; the old tri-state `'degraded'` was dropped because supervisors have no sensible action for it. `StatusBar` / `health-popover` map `'unavailable'` to the local `SubsystemState` / `SystemStatus` models; any new caller must handle the 503 path explicitly rather than assuming a 200 body. The legacy `getHealth()` export is a pure alias for `getReadiness()` and exists only so older call sites compile.

**MSW handlers (MANDATORY)**: `web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a default happy-path handler for every exported endpoint function. `test-setup.tsx` boots `setupServer(...defaultHandlers)` with `onUnhandledRequest: 'error'` so any request without a handler fails the test loudly. Tests override defaults per-case via `server.use(http.get(...))` inside the test body; never use `vi.mock('@/api/endpoints/*')`. Handler response payloads go through typed helpers keyed to the endpoint function's return type (`successFor<typeof endpoint>(data)` for `ApiResponse<T>` routes, `paginatedFor<typeof endpoint>(result)` for `PaginatedResponse<T>` routes, `voidSuccess()` for void routes), so any drift between endpoint modules and handlers fails type-check. Per-domain `buildEntity()` builders (`buildAgent`, `buildTask`, `buildWorkflow`, etc.) are exported from `@/mocks/handlers` for constructing realistic stubs in overrides.

**Test teardown**: `web/src/test-setup.tsx` registers a global `afterEach` that calls `useToastStore.getState().dismissAll()` (clears pending auto-dismiss timers + the toasts array in one idiomatic call), invokes `cancelPendingPersist()` on the notifications store, and calls `useThemeStore.getState().teardown()` (detaches the `prefers-reduced-motion` `MediaQueryList` listener installed at store creation). Tests that need to inspect the toasts list *after* timers drain can call `useToastStore.getState().cancelAllPending()` directly in their own teardown; it clears timers without mutating `toasts`. This contract is required for `npm run test -- --detect-async-leaks` to stay under the CI ceiling; **any new store that schedules timers or attaches event listeners (e.g. `matchMedia`, `document.addEventListener`, `IntersectionObserver`) must expose an equivalent cleanup hook** and register it in the global `afterEach`. The theme store also calls `teardown()` from its `import.meta.hot?.dispose(...)` block so Vite Fast Refresh does not layer duplicate listeners in dev. The theme store additionally exposes `reattach()` (the companion to `teardown()`) so tests that exercise runtime reduced-motion reactivity can re-install the listener against a per-test `window.matchMedia` mock (call it in the test body after the mock is installed; it is idempotent if the listener is already attached).

**Async-leak ceiling (MANDATORY)**: CI's `Dashboard Test` job runs `vitest run --coverage --detect-async-leaks` under `NO_COLOR=1` and fails if the `Leaks N leaks` summary line reports more than `MAX_ASYNC_LEAKS` (currently 80) OR if the anchored summary line is missing entirely. The local post-shim+M4 floor is 49; CI measures ~76 on ubuntu-latest because event-loop timing differs by platform, so the CI ceiling carries a small run-to-run variance buffer. The 2026-04-21 raise (66 -> 72) was tied to PR #1500 (WEB-1): the new WS payload sanitizers (`sanitizeApproval` / `sanitizeMeeting` / `sanitizeTask`) + strict shape guards exercise MSW-mocked paths ~6-8 more times on ubuntu-latest without introducing new timers or fire-and-forget async. The 2026-04-22 raise (72 -> 80) was tied to PR #1506 (WEB-2): nine new fast-check property suites exercise the same MSW-intercepted render paths a few more times per property, pushing the CI baseline to 76; see `.github/workflows/ci.yml` for the full per-PR justifications. The structural floor is MSW 2.x's own `CookieStore` (tough-cookie), the MSW XHR interceptor's `queueMicrotask` dispatch, and axios's response-interceptor `promise.then` chain; none reachable from `test-setup.tsx`. The 2026-04-20 research round measured and rejected two further approaches (sync `queueMicrotask` went to 114; a custom axios adapter dispatching via MSW's `handler.run` went to 76 by re-attributing Promises to MSW's parse pipeline); zero leaks requires replacing MSW's matching layer, tracked by #1468. Raise the ceiling only with the same kind of documented per-PR justification; lower it whenever the structural floor shifts. Full investigation + options matrix lives in `docs/design/web-http-adapter.md`. `test-setup.tsx` also installs a synchronous `document.cookie` shim on `Document.prototype` to bypass jsdom's tough-cookie Promise-based accessor: preserve that shim; if a unit test needs a different cookie behavior, override it via `Document.prototype.cookie` at the test level (see `__tests__/utils/csrf.test.ts`) and ensure the test's `afterEach` restores the shim by default. The shim itself is reset by the global `afterEach` in `test-setup.tsx`, which clears the jar and re-seeds `csrf_token=test-csrf-token`, so tests that mutate `document.cookie` directly do not leak state across the suite.

**WS payload sanitization**: `sanitizeWsString()` (exported from `web/src/stores/notifications.ts`) normalizes every string field received from WebSocket events before it reaches storage or display. It strips C0 control characters and DELETE (except common whitespace `\t` / `\n` / `\r`), strips bidi-override characters (CVE-2021-42574 class), trims, and caps length at `MAX_STRING_LEN` (128) at code-point boundaries so surrogate pairs are not split. Any new WS payload handler in the notifications store **or a sibling store that ingests untrusted strings** (messages, approvals, tasks, agents, ...) MUST route string fields through this sanitizer.

**WS wire protocol (MANDATORY)**: the client-server WebSocket contract lives behind a handful of constants in `web/src/utils/constants.ts` that MUST stay in lockstep with `src/synthorg/api/ws_models.py` / `src/synthorg/api/controllers/ws.py`:
- **Protocol version.** `WS_PROTOCOL_VERSION` (client) ↔ `WsEvent.version` default (server). Bump both together when introducing a breaking payload change. Events whose `version` the client does not understand are logged and dropped.
- **Frame size caps.** `WS_MAX_MESSAGE_SIZE` (32 KiB inbound on the client) mirrors the server's `_MAX_OUTBOUND_EVENT_BYTES`; a tighter 4 KiB cap on server-inbound control messages (subscribe / unsubscribe / auth / ping) never reaches the client.
- **Heartbeat.** `WS_HEARTBEAT_INTERVAL_MS` (20 s) + `WS_PONG_TIMEOUT_MS` (10 s): the client pings every 20 s and treats a missing pong after 10 s as a dead socket (triggers reconnect). 20 s sits comfortably under the typical 60 s proxy idle close.
- **Single writer.** Ping/pong (and every subscribe/unsubscribe ack) is enqueued on the server's per-connection outbound queue so `_outbound_consumer` remains the only writer; control frames cannot interleave with broadcast events mid-frame.
- **Auth handshake.** After ticket validation on both auth paths (ticket-in-URL and first-message), the server emits `{action:"auth_ok"}`; clients only flip `connected=true` once that frame lands, closing the pre-existing auth-state flash.
- **Backpressure.** Per-connection outbound is bounded by an `asyncio.Queue(maxsize=64)` of bytes. Oversized events are dropped (`API_WS_EVENT_DROPPED`) and backpressure drops (`API_WS_BACKPRESSURE_DROPPED`) keep the socket open so one slow consumer cannot nuke the channel.
- **Log sanitization.** `LOG_SANITIZE_MAX_LENGTH` caps log-injection truncation for WS error strings; use it (not a bare number) when passing untrusted fields to `sanitizeForLog`.
- **User retry.** The `retry()` action on `useWebSocketStore` is the user-initiated escape hatch from the reconnect-exhausted state; wire it into any new surface that surfaces the disconnect toast.

## Design System (MANDATORY)

### Component Reuse

**ALWAYS reuse existing components from `web/src/components/ui/`** before creating new ones. These are the shared building blocks; every page composes from them:

| Component | Import | Use for |
|-----------|--------|---------|
| `StatusBadge` | `@/components/ui/status-badge` | Agent/task/system status indicators (colored dot + optional built-in `label`). Default emits `role="img"` with an aria-label. Pass `decorative` when the badge is visually labeled by adjacent text (emits `aria-hidden`); pass `announce` for live WS updates (emits `role="status"` + `aria-live="polite"`). |
| `MetricCard` | `@/components/ui/metric-card` | Numeric KPIs with sparkline, change badge, progress bar |
| `Sparkline` | `@/components/ui/sparkline` | Inline SVG trend lines with `color?` and `animated?` props (used inside MetricCard or standalone) |
| `SectionCard` | `@/components/ui/section-card` | Titled card wrapper with icon and action slot |
| `AgentCard` | `@/components/ui/agent-card` | Agent display: avatar, name, role, status, current task |
| `DeptHealthBar` | `@/components/ui/dept-health-bar` | Department utilization: animated fill bar + `health?` (optional, shows N/A when null) + `agentCount` (required) |
| `ProgressGauge` | `@/components/ui/progress-gauge` | Circular or linear gauge for budget/utilization (`variant?` defaults to `'circular'`, `max?` defaults to 100) |
| `StatPill` | `@/components/ui/stat-pill` | Compact inline label + value pair |
| `Avatar` | `@/components/ui/avatar` | Circular initials avatar with optional `borderColor?` prop |
| `Button` | `@/components/ui/button` | Standard button (shadcn) |
| `Toast` / `ToastContainer` | `@/components/ui/toast` | Success/error/warning/info notifications with auto-dismiss queue (mount `ToastContainer` once in AppLayout). Store exposes `dismissAll()` (timers + toasts) and `cancelAllPending()` (timers only, preserves toasts) for test teardown; the global `afterEach` in `web/src/test-setup.tsx` uses `dismissAll()`. |
| `Skeleton` / `SkeletonCard` / `SkeletonMetric` / `SkeletonTable` / `SkeletonText` | `@/components/ui/skeleton` | Loading placeholders matching component shapes (shimmer animation, respects `prefers-reduced-motion`) |
| `EmptyState` | `@/components/ui/empty-state` | No-data / no-results placeholder with icon, title, description, optional action button |
| `ErrorBoundary` | `@/components/ui/error-boundary` | React error boundary with retry. `level` prop: `page` / `section` / `component` |
| `ConfirmDialog` | `@/components/ui/confirm-dialog` | Confirmation modal (Base UI AlertDialog) with `default` / `destructive` variants and `loading` state |
| `CommandPalette` | `@/components/ui/command-palette` | Global Cmd+K search (cmdk-base + Base UI Dialog + React Router). Mount once in AppLayout, register commands via `useCommandPalette` hook |
| `InlineEdit` | `@/components/ui/inline-edit` | Click-to-edit text with Enter/Escape, validation, optimistic save with rollback |
| `AnimatedPresence` | `@/components/ui/animated-presence` | Page transition wrapper (Motion AnimatePresence keyed by route) |
| `StaggerGroup` / `StaggerItem` | `@/components/ui/stagger-group` | Card entrance stagger container with configurable delay |
| `Drawer` | `@/components/ui/drawer` | Slide-in panel (Base UI Drawer, `side`: left or right, default right) with overlay, CSS transitions, focus management + swipe-to-dismiss via Base UI, Escape-to-close, optional header (`title`), `ariaLabel` for accessible name (one of `title` or `ariaLabel` required), named width variant via `width?: 'narrow' \| 'default' \| 'wide'` (default `'default'`, mapped to `--so-drawer-width-*` tokens), and `contentClassName` override |
| `InputField` | `@/components/ui/input-field` | Labeled text input with error/hint display, optional multiline textarea mode, and optional `leadingIcon` (decorative, pointer-events-none) / `trailingElement` (interactive slot, e.g. clear button) that are positioned relative to the input box, not the labeled wrapper |
| `SelectField` | `@/components/ui/select-field` | Labeled select dropdown with error/hint and placeholder support |
| `SliderField` | `@/components/ui/slider-field` | Labeled range slider with custom value formatter and aria-live display |
| `ToggleField` | `@/components/ui/toggle-field` | Labeled toggle switch (role="switch") with optional description text |
| `TaskStatusIndicator` | `@/components/ui/task-status-indicator` | Task status dot with optional label and pulse animation (accepts `TaskStatus`) |
| `PriorityBadge` | `@/components/ui/task-status-indicator` | Task priority colored pill badge (critical/high/medium/low) |
| `ProviderHealthBadge` | `@/components/ui/provider-health-badge` | Provider health status indicator (up/degraded/down/unknown colored dot + optional label) |
| `ConnectionHealthBadge` | `@/components/ui/connection-health-badge` | Integration connection health indicator (healthy/degraded/unhealthy/unknown); thin wrapper over `ProviderHealthBadge` that owns the enum mapping |
| `TokenUsageBar` | `@/components/ui/token-usage-bar` | Segmented horizontal meter bar for token usage (multi-segment with auto-colors, `role="meter"`, animated transitions) |
| `CodeMirrorEditor` | `@/components/ui/code-mirror-editor` | CodeMirror 6 editor with JSON/YAML modes, design-token dark theme, line numbers, bracket matching, `readOnly` support |
| `SegmentedControl` | `@/components/ui/segmented-control` | Accessible radiogroup with keyboard navigation, size variants (`sm`/`md`), generic `<T extends string>` typing |
| `ThemeToggle` | `@/components/ui/theme-toggle` | Base UI Popover with 5-axis theme controls (color, density, typography, animation, sidebar), rendered in StatusBar |
| `LiveRegion` | `@/components/ui/live-region` | Debounced ARIA live region wrapper (`polite`/`assertive`) for real-time WS updates without overwhelming screen readers |
| `MobileUnsupportedOverlay` | `@/components/ui/mobile-unsupported` | Full-screen overlay at `<768px` viewports directing users to desktop or CLI; self-manages visibility via `useBreakpoint` |
| `LazyCodeMirrorEditor` | `@/components/ui/lazy-code-mirror-editor` | Suspense-wrapped lazy-loaded `CodeMirrorEditor` (drop-in replacement, defers ~200KB+ CodeMirror bundle) |
| `TagInput` | `@/components/ui/tag-input` | Chip-style multi-value input with add/remove, keyboard support (Enter to add, Backspace to remove), paste splitting |
| `MetadataGrid` | `@/components/ui/metadata-grid` | Key-value metadata grid for detail pages with configurable columns (2/3/4), density-aware spacing |
| `ProjectStatusBadge` | `@/components/ui/project-status-badge` | Project status dot with optional label (planning/active/on_hold/completed/cancelled, semantic colors) |
| `ContentTypeBadge` | `@/components/ui/content-type-badge` | MIME content type pill badge with semantic colors (JSON, PDF, Image, Text, etc.) |
| `PolicySourceBadge` | `@/components/ui/policy-source-badge` | Ceremony policy field source indicator (project/department/default origin pill) |
| `InheritToggle` | `@/components/ui/inherit-toggle` | Toggle for inheriting vs. overriding a policy field from the parent level |
| `ErrorBanner` | `@/components/ui/error-banner` | Error / warning / info banner for list-fetch failures, offline state, onboarding retry guidance. `severity` maps to `role=alert` (error) or `role=status` (warning/info). `variant='offline'` forces warning + WifiOff icon. Optional `onRetry` / `onDismiss` / `action` slots. Use this for every page-level or form-level error surface; use toasts for mutation outcomes instead. |
| `Breadcrumbs` | `@/components/ui/breadcrumbs` | Breadcrumb navigation for deep detail pages. `<nav aria-label="Breadcrumb">` + `<ol>` + `aria-current="page"` on terminal item. React Router 7 `<Link>` for ancestors. Collapses middle items with ellipsis when exceeding `maxItems` (default 4). |
| `Pagination` | `@/components/ui/pagination` | List-page pagination control (client-side slice now; props-compatible for OPS-1 cursor mode). Keyboard shortcuts: Home / End / ArrowLeft / ArrowRight / PageUp / PageDown. `total=undefined` signals cursor mode (Next enabled, Last disabled). Pair with `useListPagination` hook for URL-persisted state. |
| `ListHeader` | `@/components/ui/list-header` | Standardised list-page header: title + count (formatted via `formatNumber`) on the left, `primaryAction` on the right, optional `secondaryActions` wrap below. Replaces ad-hoc `<div>` headers on list pages. |
| `SearchFilterSort` | `@/components/ui/search-filter-sort` | Layout wrapper for list-page controls (search + filters + sort). Named `search`, `filters`, `sort`, `trailing` slots. Pairs with `SearchInput` for the search slot. |
| `SearchInput` | `@/components/ui/search-input` | Search box with optional `/` global focus shortcut (`focusShortcut`), clear button, imperative `focus()` / `clear()` via React 19 `ref` prop. Named width cap via `maxWidth?: 'narrow' \| 'wide'` (default `'wide'`, mapped to `--so-search-max-*` tokens). |
| `BulkActionBar` | `@/components/ui/bulk-action-bar` | Sticky bottom action bar for multi-select list pages. `selectedCount` renders "N selected" on the left, `children` slot takes caller-supplied action buttons (e.g. destructive `Delete N`), `onClear` wires the built-in Clear button. Motion-animated entrance/exit via the approvals-originated spring preset. Used by Workflows and Projects bulk-delete flows; pair with row-level `selected` / `onToggleSelect` props on your grid/table view. |
| `ProgressIndicator` | `@/components/ui/progress-indicator` | Long-running operation progress. Variants: `determinate` (labeled bar + percentage), `indeterminate` (shimmer), `stages` (multi-step list with done / running / pending / failed). Use for fine-tuning pipelines, setup flows, provider probes. |
| `KeyboardShortcutHint` | `@/components/ui/keyboard-shortcut-hint` | Inline `<kbd>` pills for tooltip / button hints. Example: `<KeyboardShortcutHint keys={['Ctrl', 'K']} label="palette" />`. |
| `CommandCheatsheet` | `@/components/ui/command-cheatsheet` | Full-screen overlay triggered by `?` showing all registered shortcuts grouped by section. Reads from `useShortcutRegistry`. Mount once in AppLayout. |

#### Provider picker (shared between wizard and Settings)

| Component | Import | Use for |
|-----------|--------|---------|
| `PresetPickerSections` | `@/components/providers/PresetPickerSections` | Canonical three-section provider picker (Cloud / Detected / Manual). Reused on the setup wizard's Providers step (`web/src/pages/setup/ProvidersStep.tsx`) and the Settings → Providers page (`web/src/pages/ProvidersPage.tsx`). Owns no fetching -- all state and callbacks come in via props. |
| `CloudProviderGrid` | `@/components/providers/CloudProviderGrid` | Logo-and-name grid for cloud presets. Click a card to open the credential form pre-filled with that preset; already-configured presets render disabled with a "Configured" tag. |
| `DetectedLocalList` | `@/components/providers/DetectedLocalList` | "Detected on this machine" panel for local LLM servers. Hidden entirely when probing is idle and nothing was found (no banner, no X marks). Each row offers `[Add local]` and -- when a cloud counterpart exists in `LOCAL_TO_CLOUD_COUNTERPART` (currently Ollama -> Ollama Cloud) -- `[Add cloud]`. |
| `CustomConfigButton` | `@/components/providers/CustomConfigButton` | "Configure manually" entry point opening the credential form in custom-endpoint mode. |
| `ProviderLogo` | `@/components/providers/ProviderLogo` | Brand logo via `mask-image` against `bg-text-secondary` so the colour adapts cleanly to the active theme. SVGs live in `web/public/provider-logos/` (sourced from [lobe-icons](https://github.com/lobehub/lobe-icons), MIT). Falls back to a Lucide `Server` icon when the preset name is not in `KNOWN_LOGOS`. |

### Design Token Rules

- **Colors**: use Tailwind semantic classes (`text-foreground`, `bg-card`, `text-accent`, `text-success`, `bg-danger`, etc.) or CSS variables (`var(--so-accent)`). NEVER hardcode hex values in `.tsx`/`.ts` files.
- **Typography**: use `font-sans` or `font-mono` (maps to Geist tokens). NEVER set `fontFamily` directly. For in-chart text size, use `var(--so-text-micro)`, `var(--so-text-compact)`, `var(--so-text-body-sm)`.
- **Spacing**: use density-aware tokens (`p-card`, `gap-section-gap`, `gap-grid-gap`) or standard Tailwind spacing. NEVER hardcode pixel values for layout spacing.
- **Shadows/Borders**: use token variables (`var(--so-shadow-card-hover)`, `border-border`, `border-bright`).
- **Responsive widths**: use the named-variant tokens for drawers, search inputs, and popovers instead of hard-coding `w-[40vw]` / `max-w-*`. Drawers pick a width via `<Drawer width="narrow|default|wide">` (mapped to `--so-drawer-width-narrow|default|wide`); search inputs cap via `<SearchInput maxWidth="narrow|wide">` (mapped to `--so-search-max-narrow|wide`); popovers pass one of the two popover caps directly, e.g. `style={{ maxWidth: 'var(--so-popover-max-compact)' }}` for small menus or `style={{ maxWidth: 'var(--so-popover-max-wide)' }}` for larger surfaces like the health popover. The token values use `clamp()` so they adapt to viewport and density automatically; NEVER add new inline `w-[40vw]` or `max-w-lg` drawer overrides.
- **Chart SVG attributes** (Recharts, xyflow, `<svg>`): use `var(--so-stroke-hairline)` (1) or `var(--so-stroke-thin)` (1.5) for `strokeWidth`; `var(--so-chart-fill-opacity-strong)` (0.3) or `var(--so-chart-fill-opacity-subtle)` (0.15) for `stopOpacity`; `var(--so-dash-tight)` / `--so-dash-compact` / `--so-dash-medium` / `--so-dash-loose` / `--so-dash-wide` for `strokeDasharray`. Modern browsers resolve CSS variables inside SVG presentation attributes. **Exception**: xyflow's `MiniMap` props (`maskStrokeWidth`, `nodeStrokeWidth`, `nodeBorderRadius`) and Recharts' `margin` prop are typed as `number` and reject CSS vars; use numeric constants and a comment pointing to the token.
- **Currency**: NEVER hardcode currency codes (`'EUR'`, `'USD'`) or symbols (`€`, `$`) in formatter calls. Import `DEFAULT_CURRENCY` from `@/utils/currencies` and pass it to `formatCurrency(value, DEFAULT_CURRENCY)` or read the runtime currency from the company/settings store where available.
- **Locale / i18n**: NEVER hardcode BCP 47 locale strings (`'en-US'`, `'fr-FR'`) or call bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()`. Use the helpers in `@/utils/format` (`formatDateTime`, `formatDateOnly`, `formatTime`, `formatDayLabel`, `formatTodayLabel`, `formatRelativeTime`, `formatNumber`, `formatCurrency`, `formatCurrencyCompact`, `formatTokenCount`), all of which accept an optional `locale?: string` that defaults to `getLocale()` from `@/utils/locale`. The locale source of truth is `APP_LOCALE` in `@/utils/locale`; swap in a settings-store read there when we add a user-facing locale toggle.

### Creating New Components

When a new shared component is needed (not covered by the inventory above):
1. Place it in `web/src/components/ui/` with a descriptive kebab-case filename
2. Create a `.stories.tsx` file alongside it with all states (default, hover, loading, error, empty)
3. Export props as a TypeScript interface
4. Use design tokens exclusively; no hardcoded colors, fonts, or spacing
5. Import `cn` from `@/lib/utils` for conditional class merging
6. **For primitives backed by Base UI** (Dialog, AlertDialog, Popover, Menu, Tabs, Drawer; see the Adoption Decisions table below for the canonical list; `Select`, `Toast`, `Meter`, `Combobox`, `Tooltip` are intentionally **not** adopted):
   - Import from the specific subpath: `import { Dialog } from '@base-ui/react/dialog'`
   - Use the component's `render` prop for polymorphism: `<Dialog.Trigger render={<Button>Open</Button>} />`. Never spread props manually.
   - For Dialog/AlertDialog/Popover/Drawer: compose with `Portal` + `Backdrop` + `Popup`. Popover and Menu additionally require a `Positioner` wrapper that owns `side` / `align` / `sideOffset`. Drawer additionally supports `swipeDirection` on `Root` and `SwipeArea` for swipe-to-dismiss.
   - Animation state attributes are `data-[open]`, `data-[closed]`, `data-[starting-style]`, `data-[ending-style]` (not `data-[state=open]` / `data-[state=closed]`). Tabs Tab uses `data-[active]` (not `data-[state=active]`).
   - In Tailwind v4, `translate-*` and `scale-*` compile to the dedicated CSS `translate:` and `scale:` properties, not `transform:`. Transition property lists must name each one explicitly: `transition-[opacity,translate]` or `transition-[opacity,scale]`, not just `transition-[opacity,transform]`.
   - The local `<Slot>` helper in `components/ui/slot.tsx` is reserved for `<Button asChild>`; all other polymorphism goes through Base UI's `render` prop.

### What NOT to Do

- **Do NOT** recreate status dots inline; use `<StatusBadge>`
- **Do NOT** build card-with-header layouts from scratch; use `<SectionCard>`
- **Do NOT** create metric displays with `text-metric font-bold`; use `<MetricCard>`
- **Do NOT** render initials circles manually; use `<Avatar>`
- **Do NOT** create complex (>8 line) JSX inside `.map()`; extract to a shared component
- **Do NOT** use `rgba()` with hardcoded values; use design token variables

### Enforcement

A PostToolUse hook (`scripts/check_web_design_system.py`) runs automatically on every Edit/Write to `web/src/` files. It catches:
- Hardcoded hex colors and rgba values
- Hardcoded font-family declarations
- Hardcoded Motion transition durations (should use `@/lib/motion` presets)
- Hardcoded BCP 47 locale literals (`'en-US'`, `'de-DE'`, etc.) in files that use `Intl.*` or `.toLocale*String(...)`; use helpers from `@/utils/format` instead
- Bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()` calls without an explicit locale
- New components without Storybook stories
- Duplicate patterns that should use existing shared components
- Complex `.map()` blocks that should be extracted

Fix all violations before proceeding; do not suppress or ignore hook output.

## Base UI Adoption Decisions

The dashboard's primitive layer is [Base UI](https://base-ui.com).  `components.json` is set to the `base-vega` shadcn style so that any component generated via the shadcn CLI targets Base UI internals, but the adopted primitives below are **imported directly** from `@base-ui/react/*` subpaths (for example `import { Dialog } from '@base-ui/react/dialog'`) with no shadcn wrapper layer in between.  When adding a new primitive, prefer the direct-import path; do not introduce a shadcn wrapper unless there is a concrete reason to diverge.

| Component | Decision | Rationale |
|-----------|----------|-----------|
| `Dialog`, `AlertDialog`, `Popover`, `Tabs`, `Menu` | **Adopted** | Imported directly from `@base-ui/react/*` subpaths across the dashboard's primitive files and page-level dialogs. |
| `CSPProvider` | **Adopted** | Wired in `App.tsx` alongside `MotionConfig` for end-to-end nonce propagation. |
| `merge-props` | **Adopted** | Powers the local `<Slot>` helper in `components/ui/slot.tsx` (preserves the `asChild` ergonomic for `<Button>`). |
| `Toast` | **Not adopted** | Our custom `components/ui/toast.tsx` is a Zustand-backed queue that integrates with the rest of the state stack; Base UI's Toast doesn't couple to external stores. |
| `Drawer` | **Adopted** | Switched from custom Motion-based implementation to Base UI 1.4.0 stable Drawer. Base UI provides focus management (initialFocus/finalFocus), swipe-to-dismiss, modal trap focus, and CSS transitions via `data-[closed]`/`data-[starting-style]`/`data-[ending-style]` selectors (consistent with Dialog, AlertDialog, Popover). Eliminates ~100 lines of hand-rolled a11y code (focus trap, Escape handler, portal). |
| `Meter` | **Not adopted** | `ProgressGauge` already emits `role="meter"` + `aria-valuenow`/`valuemin`/`valuemax`. Base UI's Meter is a raw primitive without the styled circular/linear variants we need. |
| `Select` | **Not adopted** | `SelectField` is a native `<select>`; we intentionally keep the native mobile picker for iOS/Android UX. Replacing with a custom dropdown would lose that. |
| `Combobox`, `Autocomplete` | **Not adopted (for now)** | v1.4.0 adds passive keyboard nav + autofill improvements. No current typeahead call sites in the dashboard (connections page uses button grid, SelectField uses native `<select>`). Re-evaluate when filterable selects become a feature requirement. |
| `OTP Field` | **Not adopted (preview)** | v1.4.0 preview component for one-time password / verification code input. Evaluate when auth/2FA flows are built (post-v0.7). |

When adding new dashboard primitives, prefer Base UI components for accessibility (Dialog, AlertDialog, Popover, Tabs, Menu, Drawer) and keep the existing custom components (`SelectField`, `Toast`, `ProgressGauge`, animations) where they are; see the Adoption Decisions table above for the canonical rationale.  Tooltip is not yet adopted; reach for an existing primitive first and add a row to the table above if a real Tooltip requirement appears.

## Post-Training Reference (TypeScript 6 & Storybook 10)

These tools were released after Claude's training cutoff. Key facts for correct code generation:

### TypeScript 6.0 (https://aka.ms/ts6)

- **`baseUrl` deprecated**: will stop working in TS 7. Remove it; `paths` entries are relative to the tsconfig directory
- **`esModuleInterop` always true**: cannot be set to `false`; remove explicit `"esModuleInterop": true` to avoid deprecation warning
- **`types` defaults to `[]`**: no longer auto-discovers `@types/*`; must explicitly list needed types (e.g. `"types": ["vitest/globals"]`)
- **`DOM.Iterable` merged into `DOM`**: `"lib": ["ES2025", "DOM"]` is sufficient, no separate `DOM.Iterable`
- **`moduleResolution: "classic"` and `"node10"` removed**: use `"bundler"` or `"nodenext"`
- **`strict` defaults to `true`**: explicit `"strict": true` is redundant but harmless
- **`noUncheckedSideEffectImports` defaults to `true`**: CSS side-effect imports need type declarations (Vite's `/// <reference types="vite/client" />` covers this)
- **Last JS-based TypeScript**: TS 7.0 will be rewritten in Go. Migration tool: `npx @andrewbranch/ts5to6`

### Storybook 10 (https://storybook.js.org/docs/releases/migration-guide)

- **ESM-only**: all CJS support removed
- **Packages removed**: `@storybook/addon-essentials`, `@storybook/addon-interactions`, `@storybook/test`, `@storybook/blocks` no longer published. Essentials (backgrounds, controls, viewport, actions, toolbars, measure, outline) and interactions are built into core `storybook`
- **`@storybook/addon-docs` is separate**: must be installed and added to addons if using `tags: ['autodocs']` or MDX
- **Import paths changed**: use `storybook/test` (not `@storybook/test`), `storybook/actions` (not `@storybook/addon-actions`)
- **Type-safe config**: use `defineMain` from `@storybook/react-vite/node` and `definePreview` from `@storybook/react-vite` (must still include explicit `framework` field)
- **Backgrounds API changed**: use `parameters.backgrounds.options` (object keyed by name) + `initialGlobals.backgrounds.value` (replaces old `default` + `values` array)
- **a11y testing**: use `parameters.a11y.test: 'error' | 'todo' | 'off'` (replaces old `.element` and `.manual`). Set globally in `preview.tsx` to enforce WCAG compliance on all stories
- **Minimum versions**: Node 20.19+, Vite 5+, Vitest 3+, TypeScript 4.9+

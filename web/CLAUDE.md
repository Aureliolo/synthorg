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
npm --prefix web run bench                 # Vitest performance benchmarks (CodSpeed CPU Simulation; *.bench.ts files under web/src/__tests__/benchmarks/)
npm --prefix web run size                  # size-limit bundle-size budget check (requires `npm run build` first)
npm --prefix web run analyze               # bundle size treemap (opens stats.html)
npm --prefix web run e2e                   # Playwright visual regression tests
npm --prefix web run e2e:update            # update Playwright screenshot baselines
npm --prefix web run lighthouse            # Lighthouse performance audit (target: 90+; also runs in CI via .github/workflows/lighthouse.yml against vite preview)
npm --prefix web run storybook             # Storybook dev server (http://localhost:6006)
npm --prefix web run storybook:build       # Storybook production build
```

## Performance Benchmarks

`*.bench.ts` files under `web/src/__tests__/benchmarks/` use Vitest's `bench()` API and the `@codspeed/vitest-plugin` integration. The plugin is a no-op when `process.env.CODSPEED` is unset, so `npm run bench` works locally as a walltime sanity check. CI runs the same suite under CodSpeed CPU Simulation (deterministic instruction counting, sub-1% variance) in the `codspeed-web` job of `.github/workflows/codspeed.yml` (consolidated with the Python shard in one workflow run per CodSpeed's Sharded Benchmarks contract). Bench targets are pure-compute helpers only -- no DOM, no MSW, no Zustand store imports that pull in toast/timer side effects. New helpers worth benching live alongside their `.test.ts` counterparts under `web/src/__tests__/benchmarks/`.

Bundle-size budgets are declared in `web/.size-limit.cjs` (per-vendor-chunk gzipped ceilings) and enforced by the `dashboard-build` job in `.github/workflows/ci.yml`. Raise a budget intentionally only when a feature legitimately requires more shipping JS, never just to silence a CI red.

## Package Structure

`web/src/` follows the standard split: `api/` (Axios client + endpoint domains), `components/` (`ui/` primitives + `layout/`), `hooks/`, `lib/`, `mocks/` (MSW), `pages/`, `router/`, `stores/` (Zustand), `styles/` (design tokens), `utils/`, `__tests__/`. Stores over ~600 lines are sliced into packages with one of two aggregation patterns (package-internal `index.ts` or sibling `.ts` aggregator).

`web/e2e/` holds the Playwright suite: `factories/`, `fixtures/` (`mock-api.ts`, `websocket-harness.ts`), `flows/`, `helpers/`, `visual/`.

See [docs/reference/web-package-structure.md](../docs/reference/web-package-structure.md) for the per-folder inventory and the store-slicing pattern catalog.

## Logging

- **Always** use `createLogger` from `@/lib/logger`; never bare `console.warn`/`console.error`/`console.debug` in application code
- **Variable name**: always `log` (e.g. `const log = createLogger('module-name')`)
- **Only `logger.ts` itself** may use bare console methods
- **Levels**: `log.debug()` (DEV-only, stripped in production), `log.warn()`, `log.error()`
- **Static messages**: pass dynamic/untrusted values as separate args (not interpolated into the message string) so they go through `sanitizeArg`
- **Attacker-controlled fields** inside structured objects must be wrapped in `sanitizeForLog()` before embedding

## Zustand Store Error Handling (MANDATORY)

All store **mutation** actions (create / update / delete) follow the `stores/connections/crud-actions.ts` pattern: wrap the API call in `try` / `catch`, success path updates state + emits a success toast, failure path logs + emits an error toast + returns a sentinel (`null` for entity returns, `false` for delete). Optimistic mutations capture `previous` synchronously and restore in `catch`. **Callers MUST NOT wrap store mutation calls in `try` / `catch`**; the store owns the error UX. List reads (`fetch*`) set `error: string | null` on the store instead of toasting.

**Cursor pagination (MANDATORY)**: list endpoints use opaque cursor-based paging via `PaginationMeta`. Stores keep `nextCursor` + `hasMore` in state (not offset arithmetic) and early-return when `!hasMore || !nextCursor`. Display counts come from `data.length`; the wire envelope no longer carries `total`.

**Health / readiness endpoints (MANDATORY)**: `getLiveness()` is always 200 while the process is alive; `getReadiness()` is 200 healthy / 503 unavailable (binary `'ok' | 'unavailable'` outcome, no tri-state). Any new caller must handle the 503 path explicitly.

**MSW handlers (MANDATORY)**: `web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a default happy-path handler for every exported endpoint. `test-setup.tsx` boots with `onUnhandledRequest: 'error'`; tests override per-case via `server.use(...)`, never `vi.mock('@/api/endpoints/*')`. Typed envelope helpers (`successFor`, `paginatedFor`, `voidSuccess`) keep handlers in lockstep with endpoint return types.

**Test teardown (MANDATORY)**: `web/src/test-setup.tsx` registers a global `afterEach` that calls `useToastStore.getState().dismissAll()`, `cancelPendingPersist()` (notifications store), and `useThemeStore.getState().teardown()`. **Any new store that schedules timers or attaches event listeners must expose an equivalent cleanup hook** and register it in the global `afterEach`. The websocket store is a deliberate exception (file-local `resetStore()` in its test file).

**Async-leak ceiling (MANDATORY)**: CI fails if `vitest --detect-async-leaks` reports more than `MAX_ASYNC_LEAKS` (current value is in `.github/ci/web-async-leaks.max`). Local count runs ~75; CI runs land in a ~90-91 band (~+15 above local, event-loop timing variance under parallel execution). Raise the ceiling only when a new test surface or fixture demonstrates measurable leak growth, and document the reason in the PR body; tighten it whenever a shim or teardown lands that demonstrably lowers the steady-state count. The structural floor is MSW 2.x's XHR interceptor + axios's response-interceptor Promise chain + MSW's own tough-cookie store; zero leaks requires replacing MSW's matching layer.

**WS payload sanitization**: `sanitizeWsString()` and `sanitizeWsEnum()` live in `web/src/utils/ws-sanitize.ts` (pure helpers, re-exported from `@/stores/notifications`). `sanitizeWsString()` clamps every WS-supplied string (strips C0 controls + bidi-overrides + caps length). `sanitizeWsEnum<T>(value, allowlist, fallback, { field })` extends that with enum-allowlist validation: on unknown values it emits a structured `ws.enum.unknown` warning and returns the supplied fallback (must be a valid allowlist member), so a backend rolling out a new enum value cannot break UI rendering. Any new WS payload handler that ingests untrusted strings MUST route through one of these; raw `(sanitizeWsString(x, n) ?? '') as EnumType` casts are forbidden.

**WS wire protocol (MANDATORY)**: the client-server contract lives in `web/src/utils/constants.ts` (`WS_PROTOCOL_VERSION`, `WS_MAX_MESSAGE_SIZE`, `WS_HEARTBEAT_INTERVAL_MS`, `WS_PONG_TIMEOUT_MS`, `LOG_SANITIZE_MAX_LENGTH`) and MUST stay in lockstep with `src/synthorg/api/ws_models.py` / `src/synthorg/api/controllers/ws.py`. Bump the protocol version on both sides together for breaking payload changes.

**Error-code constants (MANDATORY)**: import `ErrorCode` and `ErrorCategory` from `@/api/types/errors` (re-exported from the generated `web/src/api/types/error-codes.gen.ts`). Discriminate on `ErrorCode.<NAME>`, never on raw integer literals. The generator (`scripts/generate_error_codes_ts.py`) reads `src/synthorg/core/error_taxonomy.py`; drift is enforced at pre-push by `scripts/check_error_codes_ts_in_sync.py`.

See [docs/reference/web-zustand-stores.md](../docs/reference/web-zustand-stores.md) for the full mutation pattern, the per-PR async-leak audit trail, the structural-floor research, the WebSocket auth handshake / backpressure / single-writer details, and the cookie shim contract.

## Design System (MANDATORY)

**ALWAYS reuse existing components from `web/src/components/ui/`** before creating new ones. NEVER hardcode hex colors, font-family declarations, pixel spacing, Motion transition durations, BCP 47 locale literals (`'en-US'`), or currency symbols / codes; use design tokens, `@/lib/motion` presets, the helpers in `@/utils/format`, and `DEFAULT_CURRENCY` from `@/utils/currencies`. Every new shared component lives in `web/src/components/ui/` with a sibling `.stories.tsx` covering all states. Base UI primitives are imported directly from `@base-ui/react/<subpath>` and use the native `render` prop for polymorphism; the local `<Slot>` helper is reserved for `<Button asChild>`.

**Component file conventions** (uniform across all 50+ shared
components in `web/src/components/ui/`):

1. The Props interface name is `<ComponentName>Props` and is exported
   from the same file (e.g. `AgentCardProps` in `agent-card.tsx`).
   This makes the contract greppable (`grep -r '<X>Props'`) and lets
   callers extend the props without re-typing the shape.
2. Every shared UI component has a sibling `<ComponentName>.stories.tsx`
   covering every meaningful state (default, hover, loading, error,
   empty, disabled where applicable). The PostToolUse hook on
   `web/src/components/ui/*.tsx` validates the convention.

A PostToolUse hook (`scripts/check_web_design_system.py`) runs on every `web/src/` edit and flags hardcoded hex / rgba / fonts / Motion durations / locale literals / bare `.toLocale*String()` calls / missing Storybook stories / duplicate component patterns / complex `.map()` blocks. Fix every violation before proceeding.

See [docs/reference/web-design-system.md](../docs/reference/web-design-system.md) for the full component inventory (badges, cards, forms, layout, feedback, animation, command palette, version rollback, provider picker), the design-token recipe book (colors, typography, spacing, shadows, responsive widths, chart SVG attributes), the Base UI integration recipe (Portal + Backdrop + Popup composition, animation state attributes, Tailwind v4 transition gotchas), and the "What NOT to do" anti-pattern list.

### Component reuse: don't recreate these inline

- Status dots -> `<StatusBadge>` (defaults to `role="img"` with aria-label; `decorative` for adjacent-labeled, `announce` for live WS updates).
- KPI displays -> `<MetricCard>` / `<Sparkline>` / `<ProgressGauge>` / `<TokenUsageBar>`.
- Cards -> `<SectionCard>` (titled wrapper with icon and action slot); `<AgentCard>`, `<DeptHealthBar>` for domain-specific.
- Form fields -> `<InputField>` / `<SelectField>` / `<SliderField>` / `<ToggleField>` / `<SegmentedControl>` / `<TagInput>` / `<SearchInput>`.
- Slide-in panels -> `<Drawer width="compact|narrow|default|wide">` (Base UI; do NOT add inline `w-[40vw]` overrides).
- Loading / empty / error states -> `<Skeleton>` family / `<EmptyState>` / `<ErrorBoundary>` / `<ErrorBanner>` / `<ProgressIndicator>`.
- List-page primitives -> `<ListHeader>` / `<SearchFilterSort>` / `<Pagination>` / `<BulkActionBar>` / `<MetadataGrid>` / `<Breadcrumbs>` / `<Collapsible>`. Page conventions: root container uses `space-y-section-gap` (the majority pattern -- `flex flex-col gap-section-gap` is equivalent but discouraged); `<ErrorBanner>` lands immediately after `<ListHeader>`, before any filter / pagination row; pages with a one-line mission statement pass it via `<ListHeader description="..." />`. List layout choice: use Kanban grouping for status-flow domains where each row's column conveys lifecycle phase (Tasks, Requests); use a flat scrollable list for queues without explicit phase semantics (Escalations, Approvals).
- Breadcrumb depth: aim for **2 or 3 levels max** in visible trails. The dashboard's information architecture is intentionally flat (every primary domain is one sidebar click away); a 4+ level trail almost always reflects a routing mistake. When natural depth exceeds 3, route the user to a flatter parent or rely on `<Breadcrumbs maxItems={...} />` to collapse middle nodes into the ellipsis (default `maxItems=4` collapses anything beyond). `<Breadcrumbs items={[]}>` returns `null` so unconditional render at the top of a page is safe.
- Empty-state derivation -> `useEmptyStateProps({ filteredCount, totalCount, filterActive, empty, filtered })` from `@/hooks/use-empty-state-props` returns `EmptyStateProps | null` so the page branches on a single value instead of duplicating the "no data ever" / "no data after filter" discriminator.
- Status / role / risk / urgency badge classes -> `STATUS_COLORS` family from `@/styles/status-colors` (typed `Record<EnumValue, string>` lookups; no inline `Record<EnumValue, string>` constants per page).
- Confirmation / toasts -> `<ConfirmDialog>` / `<Toast>` (Zustand-backed queue, NOT Base UI's Toast).
- Cmd+K / shortcuts -> `<CommandPalette>` / `<KeyboardShortcutHint>` / `<CommandCheatsheet>`.
- Animation -> `<AnimatedPresence>` / `<StaggerGroup>` / `<LiveRegion>` (debounced ARIA live for WS updates).
- Icon helpers -> never write `getXIcon(value): LucideIcon` factories that return a component reference and get called inside JSX render bodies (the `react-x/static-components` rule flags them as "components created during render"). Export a `<XIcon value={...} {...lucideProps} />` wrapper component instead, doing the lookup inside the wrapper via `createElement` (avoids a PascalCase JSX binding in the wrapper body too). See `web/src/utils/activity-event-icon.tsx` and `web/src/pages/mcp-catalog/catalog-icons.tsx` for the canonical shape. Wrapper components live in their own file (NOT alongside utility exports) so React Fast Refresh stays compatible per the `react-refresh/only-export-components` rule.
- Viewport-size reads -> `useViewportSize()` from `@/hooks/useViewportSize` (`useSyncExternalStore` over `window` resize). Never read `window.innerWidth` / `window.innerHeight` directly inside a component render body or `useMemo`; the `react-x/globals` rule will flag it and it would be stale across resizes anyway.

## ESLint (MANDATORY)

`@eslint-react/eslint-plugin` v5+ via the `recommended-type-checked` preset (requires `parserOptions.projectService: true`, configured in `web/eslint.config.js`). Explicit error-level opt-ins beyond the preset:

- `@eslint-react/web-api-no-leaked-fetch`: detect `fetch()` in effects without `AbortController` cleanup.
- `@eslint-react/no-leaked-conditional-rendering`: catch the `{count && <Foo />}` bug where `0` renders verbatim. For `ReactNode | undefined` props use `{value != null && value !== false && <jsx>}`; for compound truthiness use `Boolean(...)`.
- `@eslint-react/globals`: restrict `window` / `document` / `localStorage` / etc. inside render. Hoist offenders into a `useCallback` event handler, a `useEffect`, or a `useSyncExternalStore`-backed hook.

Lint runs via `npm --prefix web run lint` with `--max-warnings 0`. To enumerate stale `eslint-disable` directives after a rule reshuffle: `npm --prefix web run lint -- --report-unused-disable-directives-severity=warn`.

## Base UI Adoption Decisions

**Adopted (direct import from `@base-ui/react/<subpath>`)**: Dialog, AlertDialog, Popover, Tabs, Menu, Drawer, CSPProvider, merge-props.

**Not adopted**: Toast (we use a Zustand-backed queue), Meter (covered by `<ProgressGauge>`), Select (we use native `<select>` for mobile picker UX), Combobox / Autocomplete / OTP Field / Tooltip (no current call sites; revisit when needed).

See [docs/reference/web-base-ui-decisions.md](../docs/reference/web-base-ui-decisions.md) for the per-primitive rationale table.

## Post-Training Reference

TypeScript 6 and Storybook 10 were released after Claude's training cutoff. Key gotchas: TS6 `baseUrl` is deprecated and `esModuleInterop` is always true; `types` defaults to `[]` so `vitest/globals` etc. need explicit listing. Storybook 10 is ESM-only; essentials are built into core, but `@storybook/addon-docs` is now separate; imports moved to `storybook/test` and `storybook/actions`.

See [docs/reference/web-post-training.md](../docs/reference/web-post-training.md) for the full TS6 deprecation list, Storybook 10 migration recipe, and minimum versions (Node 20.19+, Vite 5+, Vitest 3+).

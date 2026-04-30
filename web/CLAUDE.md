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

`web/src/` follows the standard split: `api/` (Axios client + 38 endpoint domains), `components/` (`ui/` primitives + `layout/`), `hooks/`, `lib/`, `mocks/` (MSW), `pages/`, `router/`, `stores/` (Zustand), `styles/` (design tokens), `utils/`, `__tests__/`. Stores over ~600 lines are sliced into packages with one of two aggregation patterns (package-internal `index.ts` or sibling `.ts` aggregator).

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

**Cursor pagination (MANDATORY)**: list endpoints use opaque cursor-based paging via `PaginationMeta`. Stores keep `nextCursor` + `hasMore` in state (not offset arithmetic) and early-return when `!hasMore || !nextCursor`. `total` is nullable; derive display counts from `data.length` when `total === null`.

**Health / readiness endpoints (MANDATORY)**: `getLiveness()` is always 200 while the process is alive; `getReadiness()` is 200 healthy / 503 unavailable (binary `'ok' | 'unavailable'` outcome, no tri-state). Any new caller must handle the 503 path explicitly.

**MSW handlers (MANDATORY)**: `web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a default happy-path handler for every exported endpoint. `test-setup.tsx` boots with `onUnhandledRequest: 'error'`; tests override per-case via `server.use(...)`, never `vi.mock('@/api/endpoints/*')`. Typed envelope helpers (`successFor`, `paginatedFor`, `voidSuccess`) keep handlers in lockstep with endpoint return types.

**Test teardown (MANDATORY)**: `web/src/test-setup.tsx` registers a global `afterEach` that calls `useToastStore.getState().dismissAll()`, `cancelPendingPersist()` (notifications store), and `useThemeStore.getState().teardown()`. **Any new store that schedules timers or attaches event listeners must expose an equivalent cleanup hook** and register it in the global `afterEach`. The websocket store is a deliberate exception (file-local `resetStore()` in its test file).

**Async-leak ceiling (MANDATORY)**: CI fails if `vitest --detect-async-leaks` reports more than `MAX_ASYNC_LEAKS` (currently 90). Local floor is 49; CI baseline 77-80 (event-loop timing variance). Raise the ceiling only with documented per-PR justification; the structural floor is MSW 2.x + axios + tough-cookie and is tracked by #1468.

**WS payload sanitization**: `sanitizeWsString()` (from `web/src/stores/notifications.ts`) normalizes every string field received from WebSocket events. Any new WS payload handler that ingests untrusted strings MUST route through it.

**WS wire protocol (MANDATORY)**: the client-server contract lives in `web/src/utils/constants.ts` (`WS_PROTOCOL_VERSION`, `WS_MAX_MESSAGE_SIZE`, `WS_HEARTBEAT_INTERVAL_MS`, `WS_PONG_TIMEOUT_MS`, `LOG_SANITIZE_MAX_LENGTH`) and MUST stay in lockstep with `src/synthorg/api/ws_models.py` / `src/synthorg/api/controllers/ws.py`. Bump the protocol version on both sides together for breaking payload changes.

See [docs/reference/web-zustand-stores.md](../docs/reference/web-zustand-stores.md) for the full mutation pattern, the per-PR async-leak audit trail, the structural-floor research, the WebSocket auth handshake / backpressure / single-writer details, and the cookie shim contract.

## Design System (MANDATORY)

**ALWAYS reuse existing components from `web/src/components/ui/`** before creating new ones. NEVER hardcode hex colors, font-family declarations, pixel spacing, Motion transition durations, BCP 47 locale literals (`'en-US'`), or currency symbols / codes; use design tokens, `@/lib/motion` presets, the helpers in `@/utils/format`, and `DEFAULT_CURRENCY` from `@/utils/currencies`. Every new shared component lives in `web/src/components/ui/` with a sibling `.stories.tsx` covering all states. Base UI primitives are imported directly from `@base-ui/react/<subpath>` and use the native `render` prop for polymorphism; the local `<Slot>` helper is reserved for `<Button asChild>`.

A PostToolUse hook (`scripts/check_web_design_system.py`) runs on every `web/src/` edit and flags hardcoded hex / rgba / fonts / Motion durations / locale literals / bare `.toLocale*String()` calls / missing Storybook stories / duplicate component patterns / complex `.map()` blocks. Fix every violation before proceeding.

See [docs/reference/web-design-system.md](../docs/reference/web-design-system.md) for the full ~70-component inventory (badges, cards, forms, layout, feedback, animation, command palette, version rollback, provider picker), the design-token recipe book (colors, typography, spacing, shadows, responsive widths, chart SVG attributes), the Base UI integration recipe (Portal + Backdrop + Popup composition, animation state attributes, Tailwind v4 transition gotchas), and the "What NOT to do" anti-pattern list.

### Component reuse: don't recreate these inline

- Status dots -> `<StatusBadge>` (defaults to `role="img"` with aria-label; `decorative` for adjacent-labeled, `announce` for live WS updates).
- KPI displays -> `<MetricCard>` / `<Sparkline>` / `<ProgressGauge>` / `<TokenUsageBar>`.
- Cards -> `<SectionCard>` (titled wrapper with icon and action slot); `<AgentCard>`, `<DeptHealthBar>` for domain-specific.
- Form fields -> `<InputField>` / `<SelectField>` / `<SliderField>` / `<ToggleField>` / `<SegmentedControl>` / `<TagInput>` / `<SearchInput>`.
- Slide-in panels -> `<Drawer width="compact|narrow|default|wide">` (Base UI; do NOT add inline `w-[40vw]` overrides).
- Loading / empty / error states -> `<Skeleton>` family / `<EmptyState>` / `<ErrorBoundary>` / `<ErrorBanner>` / `<ProgressIndicator>`.
- List-page primitives -> `<ListHeader>` / `<SearchFilterSort>` / `<Pagination>` / `<BulkActionBar>` / `<MetadataGrid>` / `<Breadcrumbs>`.
- Confirmation / toasts -> `<ConfirmDialog>` / `<Toast>` (Zustand-backed queue, NOT Base UI's Toast).
- Cmd+K / shortcuts -> `<CommandPalette>` / `<KeyboardShortcutHint>` / `<CommandCheatsheet>`.
- Animation -> `<AnimatedPresence>` / `<StaggerGroup>` / `<LiveRegion>` (debounced ARIA live for WS updates).

## Base UI Adoption Decisions

**Adopted (direct import from `@base-ui/react/<subpath>`)**: Dialog, AlertDialog, Popover, Tabs, Menu, Drawer, CSPProvider, merge-props.

**Not adopted**: Toast (we use a Zustand-backed queue), Meter (covered by `<ProgressGauge>`), Select (we use native `<select>` for mobile picker UX), Combobox / Autocomplete / OTP Field / Tooltip (no current call sites; revisit when needed).

See [docs/reference/web-base-ui-decisions.md](../docs/reference/web-base-ui-decisions.md) for the per-primitive rationale table.

## Post-Training Reference

TypeScript 6 and Storybook 10 were released after Claude's training cutoff. Key gotchas: TS6 `baseUrl` is deprecated and `esModuleInterop` is always true; `types` defaults to `[]` so `vitest/globals` etc. need explicit listing. Storybook 10 is ESM-only; essentials are built into core, but `@storybook/addon-docs` is now separate; imports moved to `storybook/test` and `storybook/actions`.

See [docs/reference/web-post-training.md](../docs/reference/web-post-training.md) for the full TS6 deprecation list, Storybook 10 migration recipe, and minimum versions (Node 20.19+, Vite 5+, Vitest 3+).

# Web Dashboard

React 19 + shadcn/ui + Base UI + Tailwind CSS 4 + Motion + Zustand. Base UI primitives use the native `render` prop; the local `<Slot>` helper in `components/ui/slot.tsx` exists only for `<Button asChild>`. CSP nonces flow via `<CSPProvider>` + `<MotionConfig nonce>` in `App.tsx`; details in `docs/security.md`.

## Quick Commands

```bash
npm --prefix web install                   # install frontend deps
npm --prefix web run dev                   # dev server (http://localhost:5173)
npm --prefix web run build                 # production build
npm --prefix web run lint                  # ESLint (zero warnings enforced)
npm --prefix web run type-check            # TypeScript type checking
npm --prefix web run test                  # Vitest unit tests (coverage scoped to files changed vs origin/main)
npm --prefix web run test -- --coverage    # Full suite (matches CI; active-handle gate is built into the setupFiles)
npm --prefix web run bench                 # Vitest performance benchmarks (CodSpeed CPU Simulation; *.bench.ts files under web/src/__tests__/benchmarks/)
npm --prefix web run size                  # size-limit bundle-size budget check (requires `npm run build` first)
npm --prefix web run analyze               # bundle size treemap (opens stats.html)
npm --prefix web run e2e                   # Playwright visual regression tests
npm --prefix web run e2e:update            # update Playwright screenshot baselines
npm --prefix web run lighthouse            # Lighthouse performance audit (target: 90+; also runs in CI via .github/workflows/lighthouse.yml against vite preview)
npm --prefix web run storybook             # Storybook dev server (http://localhost:6006)
npm --prefix web run storybook:build       # Storybook production build
```

## Package Structure

Bench targets (`web/src/__tests__/benchmarks/*.bench.ts`) are pure-compute helpers only: no DOM, no MSW, no store imports that pull in toast/timer side effects. Bundle-size budgets in `web/.size-limit.cjs` are raised only when a feature legitimately requires more shipping JS, never to silence a CI red.

See [docs/reference/web-package-structure.md](../docs/reference/web-package-structure.md) for the per-folder inventory and the store-slicing pattern catalog.

## Logging

- **Always** use `createLogger` from `@/lib/logger`; never bare `console.warn`/`console.error`/`console.debug` in application code
- **Variable name**: always `log` (e.g. `const log = createLogger('module-name')`)
- **Only `logger.ts` itself** may use bare console methods
- **Levels**: `log.debug()` (DEV-only, stripped in production), `log.warn()`, `log.error()`
- **Static messages**: pass dynamic/untrusted values as separate args (not interpolated into the message string) so they go through `sanitizeArg`
- **Attacker-controlled fields** inside structured objects must be wrapped in `sanitizeForLog()` before embedding

## Zustand Store Error Handling (MANDATORY)

All store **mutation** actions (create / update / delete) follow the `stores/connections/crud-actions.ts` pattern: wrap the API call in `try` / `catch`, success path updates state + emits a success toast, failure path logs + emits an error toast + returns a sentinel on failure. The sentinel shape mirrors the mutation's return type: `null` for entity-returning mutations (`createDepartment`, `updateAgent`, etc.), `false` for boolean-returning mutations. Every void / boolean-returning mutation uses `false` regardless of whether it deletes, reorders, or updates (so `updateCompany`, `deleteTeam`, and `reorderAgents` all follow the same pattern, not just delete). Optimistic mutations capture `previous` synchronously and restore in `catch`. Use `getCrudErrorTitle(err, fallback)` (from `@/utils/errors`) on every error toast so duplicate-resource / version-conflict / generic-conflict 409s all get distinct titles. **Callers MUST NOT wrap store mutation calls in `try` / `catch`**; the store owns the error UX. List reads (`fetch*`) set `error: string | null` on the store instead of toasting.

**Cursor pagination (MANDATORY)**: list endpoints use opaque cursor-based paging via `PaginationMeta`. Stores keep `nextCursor` + `hasMore` in state (not offset arithmetic) and early-return when `!hasMore || !nextCursor`. Display counts come from `data.length`; the wire envelope no longer carries `total`.

**Health / readiness endpoints (MANDATORY)**: `getLiveness()` is always 200 while the process is alive; `getReadiness()` is 200 healthy / 503 unavailable (binary `'ok' | 'unavailable'` outcome, no tri-state). Any new caller must handle the 503 path explicitly.

**MSW handlers (MANDATORY)**: `web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a default happy-path handler for every exported endpoint. `test-setup.tsx` boots with `onUnhandledRequest: 'error'`; tests override per-case via `server.use(...)`, never `vi.mock('@/api/endpoints/*')`. Typed envelope helpers (`successFor`, `paginatedFor`, `voidSuccess`) keep handlers in lockstep with endpoint return types.

**Test teardown (MANDATORY)**: `web/src/test-setup.tsx` registers a global `afterEach` that calls `useToastStore.getState().dismissAll()`, `cancelPendingPersist()` (notifications store), and `useThemeStore.getState().teardown()`. **Any new store that schedules timers or attaches event listeners must expose an equivalent cleanup hook** and register it in the global `afterEach`. The websocket store is a deliberate exception (file-local `resetStore()` in its test file).

**Active-handle gate (MANDATORY)**: every unit test runs under `web/test-infra/active-handle-tracker.ts`, which hooks Node's `async_hooks` and fails any test that leaks an event-loop-holding resource (`Timeout`, `TCPWRAP`, `PIPEWRAP`, `FSEVENTWRAP`, etc.) attributable to a `web/src/` frame. Zero tolerance, no ceiling, no buffer. The allowlist (`web/test-infra/active-handle-allowlist.ts`) is empty and additions are an audit step (see [docs/design/web-active-handle-detection.md](../docs/design/web-active-handle-detection.md)). A new store that schedules timers / attaches listeners MUST expose a teardown hook and register it in the global `afterEach`; otherwise the gate fails the first test that triggers the schedule.

**WS payload sanitization**: `sanitizeWsString()` and `sanitizeWsEnum()` live in `web/src/utils/ws-sanitize.ts` (pure helpers imported directly from there). `sanitizeWsString()` clamps every WS-supplied string (strips C0 controls + bidi-overrides + caps length). `sanitizeWsEnum<T>(value, allowlist, fallback, { field })` extends that with enum-allowlist validation: on unknown values it emits a structured `ws.enum.unknown` warning and returns the supplied fallback (must be a valid allowlist member), so a backend rolling out a new enum value cannot break UI rendering. Any new WS payload handler that ingests untrusted strings MUST route through one of these; raw `(sanitizeWsString(x, n) ?? '') as EnumType` casts are forbidden.

**WS wire protocol (MANDATORY)**: the client-server contract lives in `web/src/utils/constants.ts` (`WS_PROTOCOL_VERSION`, `WS_MAX_MESSAGE_SIZE`, `WS_HEARTBEAT_INTERVAL_MS`, `WS_PONG_TIMEOUT_MS`, `LOG_SANITIZE_MAX_LENGTH`) and MUST stay in lockstep with `src/synthorg/api/ws_models.py` / `src/synthorg/api/controllers/ws.py`. Bump the protocol version on both sides together for breaking payload changes. Drift is enforced at pre-commit / pre-push by `scripts/check_ws_protocol_version_in_sync.py`.

**Error-code constants (MANDATORY)**: import `ErrorCode` and `ErrorCategory` from `@/api/types/errors` (re-exported from the generated `web/src/api/types/error-codes.gen.ts`). Discriminate on `ErrorCode.<NAME>`, never on raw integer literals. The generator (`scripts/generate_error_codes_ts.py`) reads `src/synthorg/core/error_taxonomy.py`; drift is enforced at pre-push by `scripts/check_error_codes_ts_in_sync.py`.

**Generated DTO types (MANDATORY)**: NEVER hand-edit `web/src/api/types/*.gen.ts`. Regenerate with `uv run python scripts/generate_dto_types_ts.py`; drift enforced at pre-push by `scripts/check_dto_types_ts_in_sync.py`. Import DTOs via the barrel (`import type { AgentConfig } from '@/api/types'`). The hand-maintained `ApiResponse<T>` / `PaginatedResponse<T>` generics in `web/src/api/types/http.ts` are the call-site shape.

See [docs/reference/web-zustand-stores.md](../docs/reference/web-zustand-stores.md) for the full mutation pattern, the per-PR async-leak audit trail, the structural-floor research, the WebSocket auth handshake / backpressure / single-writer details, and the cookie shim contract.

## Design System (MANDATORY)

**ALWAYS reuse existing components from `web/src/components/ui/`** before creating new ones. NEVER hardcode hex colours, font-family declarations, pixel spacing, Motion transition durations, BCP 47 locale literals (`'en-US'`), or currency symbols / codes; use design tokens, `@/lib/motion` presets, the helpers in `@/utils/format`, and `DEFAULT_CURRENCY` from `@/utils/currencies`. Every new shared component lives in `web/src/components/ui/` with a sibling `.stories.tsx` covering all states. A shared component is either (a) a flat `web/src/components/ui/<name>.tsx` paired with `<name>.stories.tsx` for single-component primitives, or (b) a sub-package `web/src/components/ui/<name>/` with a barrel `index.ts`, each `<SubName>.tsx` paired with `<SubName>.stories.tsx`, and shared utility / hook `.ts` files alongside (see `health-popover/` for the canonical sub-package layout). Base UI primitives are imported directly from `@base-ui/react/<subpath>` and use the native `render` prop for polymorphism; the local `<Slot>` helper is reserved for `<Button asChild>`.

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
3. Base UI primitives compose Portal + Backdrop + Popup explicitly,
   use the `render` prop for polymorphism, and rely on animation
   state attributes (`data-[open]`, `data-[closed]`) rather than the
   older `data-[state=open]` form. Tailwind v4 transition + scale
   gotchas (CSS layer ordering, `@keyframes` not inheriting layer
   precedence) are covered in
   `docs/reference/web-design-system.md` § Creating New Components.

A PostToolUse hook (`scripts/check_web_design_system.py`) runs on every `web/src/` edit and flags hardcoded hex / rgba / fonts / Motion durations / locale literals / bare `.toLocale*String()` calls / missing Storybook stories / duplicate component patterns / complex `.map()` blocks. Fix every violation before proceeding.

See [docs/reference/web-design-system.md](../docs/reference/web-design-system.md) for the full component inventory (badges, cards, forms, layout, feedback, animation, command palette, version rollback, provider picker), the design-token recipe book (colours, typography, spacing, shadows, responsive widths, chart SVG attributes), the Base UI integration recipe (Portal + Backdrop + Popup composition, animation state attributes, Tailwind v4 transition gotchas), and the "What NOT to do" anti-pattern list.

### Anti-patterns (lint-enforced)

- **Icon helpers**: NEVER write `getXIcon(value): LucideIcon` factories called inside JSX bodies (`react-x/static-components` flags them). Export a `<XIcon value={...} />` wrapper that does the lookup via `createElement` inside the wrapper body. Wrapper components live in their own file, not alongside utility exports, so `react-refresh/only-export-components` stays clean. Canonical shape: `web/src/utils/activity-event-icon.tsx`.
- **Viewport-size reads**: use `useViewportSize()` from `@/hooks/useViewportSize`. NEVER read `window.innerWidth` / `window.innerHeight` directly in a render body or `useMemo`; `react-x/globals` flags it and it would be stale across resizes anyway.

## ESLint (MANDATORY)

`@eslint-react/eslint-plugin` v5+ via the `recommended-type-checked` preset (requires `parserOptions.projectService: true`, configured in `web/eslint.config.js`). Explicit error-level opt-ins beyond the preset:

- `@eslint-react/web-api-no-leaked-fetch`: detect `fetch()` in effects without `AbortController` cleanup.
- `@eslint-react/no-leaked-conditional-rendering`: catch the `{count && <Foo />}` bug where `0` renders verbatim. For `ReactNode | undefined` props use `{value != null && value !== false && <jsx>}`; for compound truthiness use `Boolean(...)`.
- `@eslint-react/globals`: restrict `window` / `document` / `localStorage` / etc. inside render. Hoist offenders into a `useCallback` event handler, a `useEffect`, or a `useSyncExternalStore`-backed hook.
- `@typescript-eslint/no-floating-promises`: forbids unawaited promises so async work cannot survive the test that scheduled it and trip the active-handle gate.
- `@typescript-eslint/no-misused-promises` (with `checksVoidReturn: { attributes: false }`): forbids passing async functions where the callsite ignores the returned promise; React 19 `async` event handlers stay allowed via the `attributes: false` exemption, paired with the global error handler.

Lint runs via `npm --prefix web run lint` with `--max-warnings 0`. To enumerate stale `eslint-disable` directives after a rule reshuffle: `npm --prefix web run lint -- --report-unused-disable-directives-severity=warn`.

### Tiered caps + per-bucket ratchet (EPIC #2066)

The four caps (`complexity: 8`, `max-lines: 400`, `max-lines-per-function: 80`, `max-params: 5`) mirror the Python pylint thresholds and the module-size tier table in `docs/decisions/0006-tiered-module-size-policy.md`. They apply globally except in the `files: ['src/**/*.{ts,tsx}']` override block in `web/eslint.config.js`, which disables them; the override gains an `ignores:` entry per EPIC #2066 sub-issue as each bucket lands, and the final PR deletes the block entirely. New code in an already-cleaned bucket MUST respect the caps.

The canonical refactor pattern for keeping a function under `complexity: 8` is **table-driven dispatch**: replace a multi-arm `if`/`switch` ladder with a `Readonly<Record<K, V>>` lookup, optionally typed with `as const satisfies Record<K, V>` for exhaustiveness against an enum/union. See `utils/errors.ts` (`CONFLICT_MESSAGES`, `CATEGORY_TITLES`, `STATUS_TITLES`, `STATUS_FALLBACK_MESSAGES`), `utils/provider-status.ts` (`_hasRequiredCredentials` exhaustive `Record<AuthType, boolean>`), `hooks/use-list-shortcuts.ts` (`KEY_TO_ACTION`), and `hooks/useToolbarKeyboardNav.ts` (`TOOLBAR_INDEX_FNS`) for worked examples. For a function whose body is too long, extract per-stage helpers (`utils/fetch-with-retry.ts` splits the retry loop into `_decideRetryWait` / `_performRetrySleep` / `_shouldKeepRetrying`); for an oversized hook, extract sub-hooks (`hooks/usePolling.ts::usePollRefs`, `hooks/useAgentDetailData.ts::{useDetailStoreSlice, useDetailLifecycle, useDetailWebSocket}`). Sub-hook names MUST start with `use` (rules-of-hooks); no leading underscore.

## Post-Training Reference

TypeScript 6 and Storybook 10 were released after Claude's training cutoff. Key gotchas: TS6 `baseUrl` is deprecated and `esModuleInterop` is always true; `types` defaults to `[]` so `vitest/globals` etc. need explicit listing. Storybook 10 is ESM-only; essentials are built into core, but `@storybook/addon-docs` is now separate; imports moved to `storybook/test` and `storybook/actions`.

See [docs/reference/web-post-training.md](../docs/reference/web-post-training.md) for the full TS6 deprecation list, Storybook 10 migration recipe, and minimum versions (Node 20.19+, Vite 5+, Vitest 3+).

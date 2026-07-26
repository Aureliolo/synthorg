# Web Dashboard

React 19 + shadcn/ui + Base UI + Tailwind CSS 4 + Motion + Zustand. Base UI primitives use the native `render` prop; the local `<Slot>` (`components/ui/slot.tsx`) exists only for `<Button asChild>`. CSP nonces flow via `<CSPProvider>` + `<MotionConfig nonce>` in `App.tsx` (see `../docs/security.md`).

## Pure API Consumer (MANDATORY)

The dashboard is **only an API consumer** and persists **no application state client-side**; the backend is the single source of truth. The SPA hydrates from a backend GET on mount and writes every change through the REST API immediately. Every feature MUST be fully usable over the API alone.

- **No `localStorage` / `sessionStorage` / IndexedDB and no `zustand` `persist`** holding domain/app state (setup-wizard state, theme/appearance, any user/org preference): each needs a backend settings key + GET/PUT, hydrated from it, never carried across reloads.
- **Step/progress is derived from backend state, not a persisted client flag.** Stale client state is the bug class to avoid (flag-says-done-but-data-empty, and data loss on re-apply); move any client persistence of domain state backend-side.
- **The only sanctioned client storage** is non-domain transport/UX: the auth-token cookie shim and the active CSRF token.
- Enforced by `scripts/check_no_client_state_persistence.py` (PreToolUse + pre-push): flags client storage / `zustand persist(` in `web/src/` outside the auth/CSRF allowlist.

## Quick Commands

```bash
npm --prefix web install                   # install deps
npm --prefix web run dev                   # dev server (http://localhost:5173)
npm --prefix web run build                 # production build
npm --prefix web run lint                  # ESLint (zero warnings enforced)
npm --prefix web run type-check            # TypeScript type-check (pre-push runs ESLint but NOT tsc; run this yourself)
npm --prefix web run test                  # Vitest unit (coverage scoped to files changed vs origin/main)
npm --prefix web run test -- --coverage    # full suite (matches CI; active-handle gate in setupFiles)
npm --prefix web run bench                 # Vitest perf benchmarks (*.bench.ts under __tests__/benchmarks/)
npm --prefix web run size                  # size-limit budget (needs `run build` first)
npm --prefix web run e2e[:update]          # Playwright visual regression [/ baseline update]
npm --prefix web run lighthouse            # Lighthouse audit (target 90+)
npm --prefix web run storybook[:build]     # Storybook dev server (http://localhost:6006)
```

## Logging

- Always `createLogger` from `@/lib/logger` (never bare `console.*` in app code; only `logger.ts` may); variable name always `log`.
- Levels: `log.debug()` (DEV-only, stripped in prod), `log.warn()`, `log.error()`. Pass dynamic/untrusted values as separate args (not interpolated) so they go through `sanitizeArg`; wrap attacker-controlled fields in structured objects with `sanitizeForLog()`.

## Zustand Store Error Handling (MANDATORY)

- **Mutation error handling**: all create/update/delete actions follow `stores/connections/crud-actions.ts`: try/catch, success updates state + success toast, failure logs + error toast + returns a sentinel (`null` for entity-returning, `false` for void/boolean). Optimistic mutations capture `previous` and restore in catch. Use `getCrudErrorTitle(err, fallback)` from `@/utils/errors`. **Callers MUST NOT wrap store mutation calls in try/catch** (the store owns error UX). List reads set `error: string | null` instead of toasting.
- **Cursor pagination (MANDATORY)**: list endpoints page via opaque `PaginationMeta` cursors; stores keep `nextCursor` + `hasMore` (no offset arithmetic) and early-return when `!hasMore || !nextCursor`. Counts come from `data.length`.
- **Client-side list pagination (`useListPagination`)**: a page that must filter / sort / search / aggregate across the WHOLE dataset (e.g. MCP Catalog, Entity Catalog, Agents, Training roster, Custom Rules, Coordination Metrics, Decision History) loads the full set (walk every cursor page via `paginateAll`) and pages it in the browser with `useListPagination` (`?{ns}Page` / `?{ns}Size` URL params). This is the sanctioned exception to server-cursor paging: a server cursor would only see one slice, so client-side filter/sort/aggregate would be wrong. Still a pure API consumer (the full set is re-hydrated from the backend on mount; nothing is persisted client-side).
- **Health / readiness endpoints (MANDATORY)**: `getLiveness()` always 200; `getReadiness()` (`/readyz`, unauth) is 200/503 binary; `getHealthDetail()` (`/health`, read-role) returns the full breakdown. New callers handle 503 explicitly.
- **MSW handlers (MANDATORY)**: `web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a happy-path handler per endpoint; tests override via `server.use(...)`, never `vi.mock('@/api/endpoints/*')`. Use the typed envelope helpers (`successFor`, `paginatedFor`, `voidSuccess`). A handler's imports are always `import type`, from `@/api/endpoints/*` (the function, for `typeof fn`) and from `@/api/types/<domain>` (the DTO shape). A value import of an endpoint module pulls its `apiClient` runtime in and breaks unrelated mocks.
- **Test teardown (MANDATORY)**: `test-setup.tsx` registers a global `afterEach` running every cleanup hook. Any new store / stateful singleton that schedules timers, attaches listeners, or holds test-keyed state MUST expose a cleanup hook and register it there.
- **Active-handle gate (MANDATORY)**: every unit test runs under `web/test-infra/active-handle-tracker.ts`, failing any test that leaks an event-loop resource from a `web/src/` frame. Zero tolerance; the allowlist is empty and additions are an audit step.
- **WS sanitization**: route untrusted WS strings through `sanitizeWsString()` / `sanitizeWsEnum<T>()` (`utils/ws-sanitize.ts`); never raw casts. Do NOT use `makeEnumParser<T>` (for `<select>` handlers) at a WS boundary.
- **WS wire protocol (MANDATORY)**: the synced contract is in `utils/ws-constants.ts` and MUST match `api/ws_models.py` / `api/controllers/ws.py`; bump `WS_PROTOCOL_VERSION` on both sides together (gate `check_ws_protocol_version_in_sync.py`). Client-only transport tuning lives in the same file but is not part of the synced contract.
- **Error-code constants (MANDATORY)**: import `ErrorCode`/`ErrorCategory` from `@/api/types/errors`; discriminate on `ErrorCode.<NAME>`, never raw integers. Drift enforced at pre-push.
- **Generated DTO types (MANDATORY)**: NEVER hand-edit `api/types/*.gen.ts`; regenerate via `scripts/generate_dto_types_ts.py`. Drift enforced at pre-push.
- **One barrel per name (MANDATORY)**: a generated DTO or enum is imported from `@/api/types/<domain>` and nowhere else. There is no `@/api/types` index barrel: a second path to the same name makes every path look unconsumed, which is what forced knip's `types` report off. A domain module lists the names the dashboard imports (never `export *` from a `.gen` module, never a re-export of a name a sibling module already owns) and adds a new one in the same commit as the consumer that needs it; `api/types/` is the only layer that may import a `.gen` module at all; an endpoint module exports behaviour and the types it derives (`StageVerdict`, `SimulationReport`), never a DTO pass-through. knip cannot see a `.gen` bypass (generated files are in its `ignore`) nor a name with two live barrels, so ESLint carries those: `no-restricted-imports` (paths + patterns), two `no-restricted-syntax` selectors and `no-duplicate-imports` in `web/eslint.config.js`, with `__tests__/api/one-barrel-per-name.test.ts` asserting each still fires.
- Full detail: [web-zustand-stores.md](../docs/reference/web-zustand-stores.md), [web-package-structure.md](../docs/reference/web-package-structure.md).

## Design System (MANDATORY)

- **Reuse `web/src/components/ui/` before creating components.** NEVER hardcode hex colours, fonts, pixel spacing, Motion durations, BCP 47 locale literals, or currency symbols; use design tokens, `@/lib/motion` presets, `@/utils/format`, and `DEFAULT_CURRENCY` from `@/utils/currencies`.
- A shared component is either a flat `ui/<name>.tsx` + `<name>.stories.tsx`, or a sub-package `ui/<name>/` with a barrel + per-sub-component stories (canonical: `health-popover/`). Props interface is exported `<ComponentName>Props` from the file that defines the component; every component has a sibling `.stories.tsx` covering all states.
- **A sub-package barrel is the package boundary**: it exports the components a consumer can render plus their `<ComponentName>Props`, marked `/** @public */` so knip treats them as surface by rule rather than by consumption. Nothing else. A helper type, or the Props of a sub-component the barrel does not export, is unreachable surface and stays internal (siblings import it from its defining module, which also avoids closing an import cycle).
- **Anti-patterns**: no `getXIcon(): LucideIcon` factories called in JSX (export a `<XIcon value={...} />` wrapper, own file); use `useViewportSize()`, never raw `window.innerWidth` in render.
- Enforced by `scripts/check_web_design_system.py` (PostToolUse on every `web/src/` edit). Full inventory + token recipes + Base UI integration: [web-design-system.md](../docs/reference/web-design-system.md).

## ESLint (MANDATORY)

Lint runs with `--max-warnings 0`. The config is strict: `exactOptionalPropertyTypes` + `noUncheckedIndexedAccess`, the `strictTypeChecked` preset, and `recommended-type-checked`. Fix types rather than suppressing; `no-unnecessary-condition` flags are usually genuinely dead checks. Key error-level opt-ins: `react-hooks/rules-of-hooks` + `exhaustive-deps`, the leaked-fetch/observer ratchets, `no-leaked-conditional-rendering`, `no-floating-promises`, `no-misused-promises`, `no-constant-binary-expression` (`checkRelationalComparisons`). Tiered caps (`complexity: 8`, `max-lines: 400`, `max-lines-per-function: 80`, `max-params: 5`) mirror the Python tiers; the canonical under-`complexity` refactor is table-driven `Record` dispatch. Full fix patterns, rule reconciliation, deferred rules, and caps: [web-eslint.md](../docs/reference/web-eslint.md).

## Post-training reference

TypeScript 6 and Storybook 10 post-date Claude's cutoff: TS6 `baseUrl` deprecated, `esModuleInterop` always true, `types` defaults to `[]`; Storybook 10 is ESM-only, `@storybook/addon-docs` separate, imports moved to `storybook/test` + `storybook/actions`. Detail + minimum versions: [web-post-training.md](../docs/reference/web-post-training.md).

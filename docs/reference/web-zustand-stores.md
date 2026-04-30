---
title: Web Zustand Stores
description: Mutation error-handling pattern, cursor pagination, MSW handler contract, test teardown, async-leak ceiling, and the WebSocket wire protocol for the React 19 dashboard.
---

# Web Zustand Stores

On-demand reference. The short rules in `web/CLAUDE.md` are: store mutations own the error UX (callers do not `try`/`catch` around them); list reads use opaque cursor pagination; every endpoint has a happy-path MSW handler; new stores that hold timers or DOM listeners expose a teardown hook so the async-leak ceiling stays under control.

## Mutation Action Pattern (MANDATORY)

All Zustand store **mutation** actions (create / update / delete) MUST follow the `stores/connections/crud-actions.ts` pattern:

1. Wrap the API call in `try` / `catch`.
2. On success: update the store + emit a success toast via `useToastStore.getState().add({ variant: 'success', title: '...' })`.
3. On failure: log via `log.error('...', sanitizeForLog(err))`, emit an error toast with `description: getErrorMessage(err)`, and **return a sentinel** (`null` for create / update returning an entity, `false` for delete returning a boolean).
4. For optimistic mutations, capture `previous` state synchronously and restore it in the `catch` branch.

**Callers MUST NOT wrap store mutation calls in `try` / `catch`**; the store owns the error UX. Callers only need to null-check the sentinel to decide whether to navigate, dismiss a dialog, or run a rollback.

**List reads** (`fetch*`) follow the same pattern for logging but set `error: string | null` on the store instead of toasting; the UI surface (usually a page-level error banner) consumes the error state.

## Cursor Pagination (MANDATORY)

List endpoints use opaque cursor-based paging. The wire envelope is `PaginationMeta { limit, next_cursor: string | null, has_more: boolean, total: number | null, offset: number }`, unwrapped in `@/api/client` to `PaginatedResult<T> { data, total, offset, limit, nextCursor, hasMore, pagination }`.

Stores that expose `fetchMore*` actions keep `nextCursor` + `hasMore` in state (not offset arithmetic) and early-return when `!hasMore || !nextCursor`; call sites pass `{ cursor: state.nextCursor, limit }` to the endpoint.

`total` is nullable: repo-backed endpoints omit the extra `COUNT(*)` round-trip, so derive display counts from `data.length` when `total === null`. `has_more=true` with `next_cursor=null` is rejected server-side by `PaginationMeta._validate_cursor_consistency`; the two fields always agree.

## Health / Readiness Endpoints (MANDATORY)

The dashboard calls:

- `getLiveness()` (`/api/v1/healthz`): always 200 while the process is alive.
- `getReadiness()` (`/api/v1/readyz`): 200 on healthy persistence + message bus, 503 otherwise.

`ReadinessOutcome` is a binary `'ok' | 'unavailable'` union; the old tri-state `'degraded'` was dropped because supervisors have no sensible action for it. `StatusBar` / `health-popover` map `'unavailable'` to the local `SubsystemState` / `SystemStatus` models; any new caller must handle the 503 path explicitly rather than assuming a 200 body. The legacy `getHealth()` export is a pure alias for `getReadiness()` and exists only so older call sites compile.

## MSW Handlers (MANDATORY)

`web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a default happy-path handler for every exported endpoint function. `test-setup.tsx` boots `setupServer(...defaultHandlers)` with `onUnhandledRequest: 'error'` so any request without a handler fails the test loudly.

Tests override defaults per-case via `server.use(http.get(...))` inside the test body; never use `vi.mock('@/api/endpoints/*')`. Handler response payloads go through typed helpers keyed to the endpoint function's return type (`successFor<typeof endpoint>(data)` for `ApiResponse<T>` routes, `paginatedFor<typeof endpoint>(result)` for `PaginatedResponse<T>` routes, `voidSuccess()` for void routes), so any drift between endpoint modules and handlers fails type-check. Per-domain `buildEntity()` builders (`buildAgent`, `buildTask`, `buildWorkflow`, etc.) are exported from `@/mocks/handlers` for constructing realistic stubs in overrides.

## Test Teardown (MANDATORY for every store)

`web/src/test-setup.tsx` registers a global `afterEach` that:

- Calls `useToastStore.getState().dismissAll()` (clears pending auto-dismiss timers + the toasts array in one idiomatic call).
- Invokes `cancelPendingPersist()` on the notifications store.
- Calls `useThemeStore.getState().teardown()` (detaches the `prefers-reduced-motion` `MediaQueryList` listener installed at store creation).

Tests that need to inspect the toasts list *after* timers drain can call `useToastStore.getState().cancelAllPending()` directly in their own teardown; it clears timers without mutating `toasts`.

This contract is required for `npm run test -- --detect-async-leaks` to stay under the CI ceiling. **Any new store that schedules timers or attaches event listeners** (e.g. `matchMedia`, `document.addEventListener`, `IntersectionObserver`) **must expose an equivalent cleanup hook** and register it in the global `afterEach`.

The theme store also calls `teardown()` from its `import.meta.hot?.dispose(...)` block so Vite Fast Refresh does not layer duplicate listeners in dev. It additionally exposes `reattach()` (the companion to `teardown()`) so tests that exercise runtime reduced-motion reactivity can re-install the listener against a per-test `window.matchMedia` mock (call it in the test body after the mock is installed; it is idempotent if the listener is already attached).

### WebSocket store is a deliberate exception

`useWebSocketStore` exposes its own `teardown()` action (clears heartbeat / pong / reconnect timers, detaches socket event handlers, bumps `connectGeneration` to invalidate stale `doConnect` chains, resets observable state including `reconnectExhausted`) but is invoked from the file-local `resetStore()` in `web/src/__tests__/stores/websocket.test.ts`, NOT from the global `afterEach`. Wiring it into the global hook would eagerly import the apiClient chain in test-setup, which captures the unmocked `getCsrfToken` reference before tests that `vi.mock('@/utils/csrf')` can hoist; see PR #1603 commit `fcfddf30` for the diagnostic. The heartbeat tests pair this with `retry: 3` on three structurally-racy cases (matching the existing `first-message auth` precedent) to absorb the residual MSW-vs-fake-timer microtask race.

## Async-Leak Ceiling (MANDATORY)

CI's `Dashboard Test` job runs `vitest run --coverage --detect-async-leaks` under `NO_COLOR=1` and fails if the `Leaks N leaks` summary line reports more than `MAX_ASYNC_LEAKS` (currently 90) OR if the anchored summary line is missing entirely.

The local post-shim+M4 floor is 49; CI measures around 76 on ubuntu-latest because event-loop timing differs by platform, so the CI ceiling carries a small run-to-run variance buffer.

### Per-PR raises (audit trail)

- **2026-04-21 (66 to 72)** tied to PR #1500 (WEB-1): the new WS payload sanitizers (`sanitizeApproval` / `sanitizeMeeting` / `sanitizeTask`) + strict shape guards exercise MSW-mocked paths roughly 6 to 8 more times on ubuntu-latest without introducing new timers or fire-and-forget async.
- **2026-04-22 (72 to 80)** tied to PR #1506 (WEB-2): nine new fast-check property suites exercise the same MSW-intercepted render paths a few more times per property, pushing the CI baseline to 76; see `.github/workflows/ci.yml` for the full per-PR justifications.
- **2026-04-30 (80 to 90)** tied to PR #1689 (docs/context-budget-trim): the docs-only PR added zero test surface but its CI run landed at 81 leaks. Recent main runs measured 77, 78, 79, 80 (variance band of 3), which means the previous 80 ceiling sat at the variance edge with no buffer. Bumped to 90 to restore a 10-leak headroom above the current main median. Per-test attribution via `--reporter=verbose` confirmed every attributed leak is a structural MSW interceptor leak (not test-code bug); the structural floor described above is the only available reduction path until #1468 lands.

### Structural floor

The structural floor is MSW 2.x's own `CookieStore` (tough-cookie), the MSW XHR interceptor's `queueMicrotask` dispatch, and axios's response-interceptor `promise.then` chain; none reachable from `test-setup.tsx`.

The 2026-04-20 research round measured and rejected two further approaches: sync `queueMicrotask` went to 114; a custom axios adapter dispatching via MSW's `handler.run` went to 76 by re-attributing Promises to MSW's parse pipeline. Zero leaks requires replacing MSW's matching layer, tracked by #1468.

Raise the ceiling only with the same kind of documented per-PR justification; lower it whenever the structural floor shifts. Full investigation + options matrix lives in `docs/design/web-http-adapter.md`.

### Cookie shim contract

`test-setup.tsx` installs a synchronous `document.cookie` shim on `Document.prototype` to bypass jsdom's tough-cookie Promise-based accessor: preserve that shim. If a unit test needs a different cookie behavior, override it via `Document.prototype.cookie` at the test level (see `__tests__/utils/csrf.test.ts`) and ensure the test's `afterEach` restores the shim by default. The shim itself is reset by the global `afterEach` in `test-setup.tsx`, which clears the jar and re-seeds `csrf_token=test-csrf-token`, so tests that mutate `document.cookie` directly do not leak state across the suite.

## WS Payload Sanitization

`sanitizeWsString()` (exported from `web/src/stores/notifications.ts`) normalizes every string field received from WebSocket events before it reaches storage or display. It strips C0 control characters and DELETE (except common whitespace `\t` / `\n` / `\r`), strips bidi-override characters (CVE-2021-42574 class), trims, and caps length at `MAX_STRING_LEN` (128) at code-point boundaries so surrogate pairs are not split.

Any new WS payload handler in the notifications store **or a sibling store that ingests untrusted strings** (messages, approvals, tasks, agents, ...) MUST route string fields through this sanitizer.

## WS Wire Protocol (MANDATORY)

The client-server WebSocket contract lives behind a handful of constants in `web/src/utils/constants.ts` that MUST stay in lockstep with `src/synthorg/api/ws_models.py` / `src/synthorg/api/controllers/ws.py`.

### Protocol version

`WS_PROTOCOL_VERSION` (client) ↔ `WsEvent.version` default (server). Bump both together when introducing a breaking payload change. Events whose `version` the client does not understand are logged and dropped.

### Frame size caps

`WS_MAX_MESSAGE_SIZE` (32 KiB inbound on the client) mirrors the server's `_MAX_OUTBOUND_EVENT_BYTES`; a tighter 4 KiB cap on server-inbound control messages (subscribe / unsubscribe / auth / ping) never reaches the client.

### Heartbeat

`WS_HEARTBEAT_INTERVAL_MS` (20s) + `WS_PONG_TIMEOUT_MS` (10s): the client pings every 20s and treats a missing pong after 10s as a dead socket (triggers reconnect). 20s sits comfortably under the typical 60s proxy idle close.

### Single writer

Ping / pong (and every subscribe / unsubscribe ack) is enqueued on the server's per-connection outbound queue so `_outbound_consumer` remains the only writer; control frames cannot interleave with broadcast events mid-frame.

### Auth handshake

After ticket validation on both auth paths (ticket-in-URL and first-message), the server emits `{action:"auth_ok"}`; clients only flip `connected=true` once that frame lands, closing the pre-existing auth-state flash.

### Backpressure

Per-connection outbound is bounded by an `asyncio.Queue(maxsize=64)` of bytes. Oversized events are dropped (`API_WS_EVENT_DROPPED`) and backpressure drops (`API_WS_BACKPRESSURE_DROPPED`) keep the socket open so one slow consumer cannot nuke the channel.

### Log sanitization

`LOG_SANITIZE_MAX_LENGTH` caps log-injection truncation for WS error strings; use it (not a bare number) when passing untrusted fields to `sanitizeForLog`.

### User retry

The `retry()` action on `useWebSocketStore` is the user-initiated escape hatch from the reconnect-exhausted state; wire it into any new surface that surfaces the disconnect toast.

## See also

- [web-design-system.md](web-design-system.md): component inventory and design-token rules.
- [web-package-structure.md](web-package-structure.md): `web/src/stores/` slicing patterns and where store packages live.

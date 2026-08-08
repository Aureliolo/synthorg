---
title: Web Zustand Stores
description: Mutation error-handling pattern, cursor pagination, MSW handler contract, test teardown, active-handle gate, and the WebSocket wire protocol for the React 19 dashboard.
---

# Web Zustand Stores

On-demand reference. The short rules in `web/CLAUDE.md` are: store mutations own the error UX (callers do not `try`/`catch` around them); list reads use opaque cursor pagination; every endpoint has a happy-path MSW handler; new stores that hold timers or DOM listeners expose a teardown hook so the active-handle gate does not fail their tests.

## Mutation Action Pattern (MANDATORY)

All Zustand store **mutation** actions (create / update / delete) MUST follow the `stores/connections/crud-actions.ts` pattern:

1. Wrap the API call in `try` / `catch`.
2. On success: update the store + emit a success toast via `useToastStore.getState().add({ variant: 'success', title: '...' })`.
3. On failure: log via `log.error('...', sanitizeForLog(err))`, emit an error toast with `description: getErrorMessage(err)`, and **return a sentinel** (`null` for create / update returning an entity, `false` for delete returning a boolean).
4. For optimistic mutations, capture `previous` state synchronously and restore it in the `catch` branch.

**Callers MUST NOT wrap store mutation calls in `try` / `catch`**; the store owns the error UX. Callers only need to null-check the sentinel to decide whether to navigate, dismiss a dialog, or run a rollback.

**List reads** (`fetch*`) follow the same pattern for logging but set `error: string | null` on the store instead of toasting; the UI surface (usually a page-level error banner) consumes the error state.

### When a read may use a discriminated `LoadState` instead

The flat `{ data, loading, error }` shape is the default and stays the default. `useHealthStore` is the sanctioned exception, and the bar for another one is the same: **the surfaces must be unable to render a combination the backend never reported.** Health has three such combinations (a snapshot with an error beside it, a refresh that has blanked its own snapshot, an error still displaying stale cards as current), and every consumer reads the same snapshot, so a flat shape would have each consumer re-deriving which fields to trust. `LoadState`'s `loading` variant carries the snapshot it is refreshing over, and one exported resolver (`renderedSnapshot`) answers "what should I render", so a refresh cannot half-apply across the pill and the dialog.

Reach for `LoadState` only on that argument. "The union is tidier" is not it: a list page with an error banner has no impossible combination to prevent, and paying a discriminant there just makes two stores in the codebase read differently for no invariant.

## Cursor Pagination (MANDATORY)

List endpoints use opaque cursor-based paging. The wire envelope is `PaginationMeta { limit, next_cursor: string | null, has_more: boolean, total: number | null, offset: number }`, unwrapped in `@/api/client` to `PaginatedResult<T> { data, total, offset, limit, nextCursor, hasMore, pagination }`.

Stores that expose `fetchMore*` actions keep `nextCursor` + `hasMore` in state (not offset arithmetic) and early-return when `!hasMore || !nextCursor`; call sites pass `{ cursor: state.nextCursor, limit }` to the endpoint.

`total` is nullable: repo-backed endpoints omit the extra `COUNT(*)` round-trip, so derive display counts from `data.length` when `total === null`. `has_more=true` with `next_cursor=null` is rejected server-side by `PaginationMeta._validate_cursor_consistency`; the two fields always agree.

## Health / Readiness Endpoints (MANDATORY)

The dashboard calls:

- `getLiveness()` (`/api/v1/healthz`): always 200 while the process is alive.
- `getReadiness()` (`/api/v1/readyz`): 200 when every configured dependency (persistence, message bus, providers) is healthy, 503 otherwise. The body is topology-free **and version-free** (binary outcome + uptime): both are unauthenticated, and an exact build version reveals to an anonymous caller which advisories apply.
- `getHealthDetail()` (`/api/v1/health`): authenticated per-component breakdown (persistence / message bus / providers / telemetry / memory / backup) plus the build version, for the health surfaces; requires a read-access role. 200 healthy / 503 unavailable. Takes an optional `AbortSignal` so a caller can release the request rather than only ignore its result.

`ReadinessOutcome` is a binary `'ok' | 'unavailable'` union. Supervisors have no sensible action for a partial-degraded state, so the binary is intentional. Any caller of `getReadiness()` must handle the 503 path explicitly rather than assuming a 200 body.

**Both operator-facing health surfaces read one `/health` snapshot** from `useHealthStore`, mapped to `SubsystemState` by `deriveHealthSubsystemStates`: the `StatusBar` pill and the `health-popover` dialog it opens answer the same question, so they cannot report different verdicts, and the refresh button in the dialog refreshes what the pill shows. Neither consumes `/readyz`: it is binary, carries no component topology, and a subsystem may abstain from its verdict on purpose (an unwired memory backend does, rather than take a serving deployment offline), so it cannot say which subsystem needs attention.

The derivation returns two roll-ups, neither named as the default: `withWebSocketState` folds in the WebSocket verdict and is for the dialog hero, which renders a WebSocket card beside the others; `backendOnlyState` excludes it and is for the pill, which applies its own WebSocket priority (feeding it the WebSocket-inclusive roll-up would count the stream twice under two orderings and report degraded at first paint). `backup` is reported but excluded from the readiness roll-up on both sides, and absent reads `degraded` rather than `down`: a deployment with no backup coverage still serves every request.

`LoadState`'s `loading` variant carries the snapshot it is refreshing over, so a poll tick or a dialog open does not wipe a good snapshot back to "checking...". A superseded or cancelled probe is **aborted**, not merely ignored: `latestProbe` alone would leave the request holding a socket for the client's full timeout on behalf of a surface that has gone away, which the active-handle gate reads as a leak. `cancelProbe()` is what a consumer calls as it goes away; it falls back to the carried snapshot under its own original timestamp, so nothing is left stuck on "checking..." and nothing claims to be fresher than it is.

## MSW Handlers (MANDATORY)

`web/src/mocks/handlers/` mirrors `web/src/api/endpoints/*.ts` 1:1 with a default happy-path handler for every exported endpoint function. `test-setup.tsx` boots `setupServer(...defaultHandlers)` with `onUnhandledRequest: 'error'` so any request without a handler fails the test loudly.

Tests override defaults per-case via `server.use(http.get(...))` inside the test body; never use `vi.mock('@/api/endpoints/*')`. Handler response payloads go through typed helpers keyed to the endpoint function's return type (`successFor<typeof endpoint>(data)` for `ApiResponse<T>` routes, `paginatedFor<typeof endpoint>(result)` for `PaginatedResponse<T>` routes, `voidSuccess()` for void routes), so any drift between endpoint modules and handlers fails type-check. Per-domain `buildEntity()` builders (`buildAgent`, `buildTask`, `buildWorkflow`, etc.) are exported from `@/mocks/handlers` for constructing realistic stubs in overrides.

## Test Teardown (MANDATORY for every store)

`web/src/test-setup.tsx` registers a global `afterEach` that:

- Calls `useToastStore.getState().dismissAll()` (clears pending auto-dismiss timers + the toasts array in one idiomatic call).
- Invokes `cancelPendingPersist()` on the notifications store.
- Calls `useThemeStore.getState().teardown()` (detaches the `prefers-reduced-motion` `MediaQueryList` listener installed at store creation).

Tests that need to inspect the toasts list *after* timers drain can call `useToastStore.getState().cancelAllPending()` directly in their own teardown; it clears timers without mutating `toasts`.

This contract is required for the active-handle gate to pass. **Any new store that schedules timers or attaches event listeners** (e.g. `matchMedia`, `document.addEventListener`, `IntersectionObserver`) **must expose an equivalent cleanup hook** and register it in the global `afterEach`; otherwise the first test that triggers the schedule will fail with a `Timeout` / listener leak attributed to the store.

The theme store also calls `teardown()` from its `import.meta.hot?.dispose(...)` block so Vite Fast Refresh does not layer duplicate listeners in dev. It additionally exposes `reattach()` (the companion to `teardown()`) so tests that exercise runtime reduced-motion reactivity can re-install the listener against a per-test `window.matchMedia` mock (call it in the test body after the mock is installed; it is idempotent if the listener is already attached).

### WebSocket store is a deliberate exception

`useWebSocketStore` exposes its own `teardown()` action (clears heartbeat / pong / reconnect timers, detaches socket event handlers, bumps `connectGeneration` to invalidate stale `doConnect` chains, resets observable state including `reconnectExhausted`, `sseFallbackActive`, `sseFallbackExhausted`, and `protocolVersionMismatch`) but is invoked from the file-local `resetStore()` in `web/src/__tests__/stores/websocket.test.ts`, NOT from the global `afterEach`. Wiring it into the global hook would eagerly import the apiClient chain in test-setup, which captures the unmocked `getCsrfToken` reference before tests that `vi.mock('@/utils/csrf')` can hoist; see PR #1603 commit `fcfddf30` for the diagnostic. The heartbeat tests pair this with `retry: 3` on three structurally-racy cases (matching the existing `first-message auth` precedent) to absorb the residual MSW-vs-fake-timer microtask race.

## Active-Handle Gate (MANDATORY)

The unit project loads `web/test-infra/active-handle-tracker.ts` as a setupFile. The tracker hooks Node's `async_hooks` and fails any test that leaves an event-loop-holding resource attributable to a `web/src/` frame live past `afterEach`. Zero tolerance, no ceiling, no allowlist (`web/test-infra/active-handle-allowlist.ts` is empty by design). See [docs/design/web-active-handle-detection.md](../design/web-active-handle-detection.md) for the full design, telemetry shape, and how to add an allowlist entry (you almost never should).

### Test-environment shims

`test-setup.tsx` installs two shims that bypass jsdom asynchronous primitives whose per-call work would otherwise slow tests down. The shims are speed optimisations; the active-handle gate would tolerate the original jsdom paths if they did not also schedule per-write `setTimeout(0)` dispatches that compound across thousands of tests.

- **Cookie shim** (`@/cookie-shim`): replaces `Document.prototype.cookie` with a synchronous in-memory jar so jsdom's tough-cookie Promise-based accessor is bypassed. The jar is exported so tests can wipe per-test state without touching the DOM. The global `afterEach` clears the jar and re-seeds `csrf_token=test-csrf-token`. Tests that need different cookie behaviour override `Document.prototype.cookie` at the test level (see `__tests__/utils/csrf.test.ts`) and restore the shim in their own `afterEach`. Prototype-pollution defence is the primary security purpose; speed is a side-benefit.
- **Storage shim** (`@/storage-shim`): patches `Storage.prototype` methods to bypass jsdom's `_dispatchStorageEvent` `setTimeout(0)` path. `localStorage instanceof Storage` stays true and `vi.spyOn(Storage.prototype, 'setItem' | 'getItem' | ...)` continues to intercept. State is held in an instance-keyed `WeakMap`; per-test isolation is the caller's responsibility (see `cancelSetupWizardPersist`, `cancelOrgChartPrefsPersist`, `cancelPendingPersist`).

### Companion ESLint rules

`@typescript-eslint/no-floating-promises` and `@typescript-eslint/no-misused-promises` (with `checksVoidReturn: { attributes: false }` for the React event-handler pattern) are enabled at error level. They catch the most common syntactic shapes that lead to forgotten async work at edit time; the active-handle gate catches anything that actually allocates a handle at runtime.

## WS Payload Sanitization

`sanitizeWsString()` (exported from `web/src/stores/notifications.ts`) normalises every string field received from WebSocket events before it reaches storage or display. It strips C0 control characters and DELETE (except common whitespace `\t` / `\n` / `\r`), strips bidi-override characters (CVE-2021-42574 class), trims, and caps length at `MAX_STRING_LEN` (128) at code-point boundaries so surrogate pairs are not split.

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

### SSE fallback transport

When the WebSocket handshake fails twice consecutively with a 1006 close before `auth_ok` (the canonical proxy-blocked-upgrade signature), the store switches to a read-only SSE feed against `/api/v1/events/dashboard` (`web/src/api/sse/client.ts` + `web/src/stores/websocket/sse-fallback.ts`). That session-less, channel-multiplexed endpoint (`src/synthorg/api/controllers/events/_dashboard.py`) forwards the same `WsEvent` payloads the WebSocket serves, one per named `ws` frame; the fallback validates each frame via `isWsEvent` and routes it through the same `dispatchEvent` chain so tasks / agents / approvals / budget keep updating; write-path features stay gated behind the "connection limited" toast. Three observable flags drive the UI:

- `sseFallbackActive`: the SSE feed is live (WS is down). `WsConnectionBanner` renders a *degraded* (`severity="warning"`) state, not the hard *offline* state.
- `sseFallbackExhausted`: the SSE feed itself exhausted `SSE_MAX_RECONNECT_ATTEMPTS` (the `EventSource` is closed; no live transport remains). The banner escalates to the offline / retry state.
- `protocolVersionMismatch`: the server emitted events whose `version` the client repeatedly could not parse, set after a threshold so a banner can advise a reload.

So `WsConnectionBanner` has three states, not two: *connected* (hidden), *degraded* (SSE fallback active), and *offline* (no transport / fallback exhausted). Because the client drives reconnect itself (a fresh `EventSource` each cycle, not the browser's native retry), it records each frame's `lastEventId` (clamped via `sanitizeWsString`) and threads it back as a `?last_event_id=` query parameter so the backend replays the recent per-channel backlog on reconnect.

### SSE / polling constants

Alongside the WS wire constants, `web/src/utils/constants.ts` carries `SSE_MAX_RECONNECT_ATTEMPTS` (SSE fallback reconnect budget) and `INTERRUPTS_POLL_INTERVAL` (interrupt-panel poll cadence while WS is down). These are client-only (no server counterpart) and so are not covered by the protocol-version drift gate.

## See also

- [web-design-system.md](web-design-system.md): component inventory and design-token rules.
- [web-package-structure.md](web-package-structure.md): `web/src/stores/` slicing patterns and where store packages live.

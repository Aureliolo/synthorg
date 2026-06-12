import type { StoreApi } from 'zustand'
import type {
  WsChannel,
  WsEventHandler,
  WsSubscriptionFilters,
} from '@/api/types/websocket'

export interface WebSocketState {
  connected: boolean
  reconnectExhausted: boolean
  /** True while the read-only SSE fallback transport is actively delivering events. */
  sseFallbackActive: boolean
  /** True when the SSE fallback exhausted its reconnect budget and gave up. */
  sseFallbackExhausted: boolean
  /** True when inbound events repeatedly fail the wire-version check (server/client protocol drift). */
  protocolVersionMismatch: boolean
  subscribedChannels: readonly WsChannel[]

  connect: () => Promise<void>
  disconnect: () => void
  /**
   * Reset reconnect bookkeeping after the user explicitly asks for a
   * fresh attempt -- usually wired to a "Retry" button surfaced on the
   * reconnect-exhausted toast/badge.
   */
  retry: () => Promise<void>
  subscribe: (channels: WsChannel[], filters?: WsSubscriptionFilters) => void
  unsubscribe: (channels: WsChannel[]) => void
  onChannelEvent: (channel: WsChannel | '*', handler: WsEventHandler) => void
  offChannelEvent: (channel: WsChannel | '*', handler: WsEventHandler) => void
  /**
   * Non-throwing teardown helper for channel subscriptions. Removes
   * each ``(channel, handler)`` binding and then unsubscribes the
   * channel set. The store owns all error UX -- callers never wrap
   * this in ``try``/``catch``.
   */
  rollbackSubscriptions: (
    channels: readonly WsChannel[],
    bindings: readonly { channel: WsChannel; handler: WsEventHandler }[],
    options?: { unsubscribe?: boolean },
  ) => void
  /**
   * Synchronous, idempotent teardown hook used by tests to start
   * from a clean slate. Resets every module-scope handle
   * (heartbeat / pong / reconnect timers, socket reference, generation
   * counter, subscription bookkeeping, channel handlers) and the
   * observable store state, including ``reconnectExhausted`` which
   * ``disconnect()`` deliberately leaves alone.
   *
   * Canonical "I am about to start a fresh test" hook; relying on
   * ``disconnect()`` alone is insufficient because
   * (a) ``disconnect()`` does not reset ``reconnectExhausted``, and
   * (b) a prior test that crashed before its own teardown could leave
   * a stale ``setInterval`` armed under fake timers, and the next test
   * advancing timers would re-trigger that interval against a freshly
   * mocked WebSocket.
   *
   * Wiring contract: invoked from the file-local ``resetStore()`` in
   * ``web/src/__tests__/stores/websocket.test.ts`` (NOT from the global
   * ``afterEach`` in ``test-setup.tsx``). The global hook would eagerly
   * import the apiClient chain and capture the unmocked
   * ``getCsrfToken`` reference before tests that ``vi.mock('@/utils/csrf')``
   * can hoist; web/CLAUDE.md ("Test teardown") documents the carve-out.
   */
  teardown: () => void
}

export type WsSet = StoreApi<WebSocketState>['setState']
export type WsGet = StoreApi<WebSocketState>['getState']

/**
 * WebSocket connection state management (Zustand).
 *
 * Manages ticket-based auth, exponential backoff reconnection, channel-based
 * subscriptions with handler deduplication, and auto-re-subscribe on reconnect.
 */

import { create } from 'zustand'
import { AxiosError } from 'axios'
import { openSseFallback } from '@/api/sse/client'
import { WS_CHANNELS } from '@/api/types/websocket'
import type { WsChannel, WsEvent, WsEventHandler, WsSubscriptionFilters } from '@/api/types/websocket'
import { getWsTicket } from '@/api/endpoints/auth'
import {
  LOG_SANITIZE_MAX_LENGTH,
  WS_HEARTBEAT_INTERVAL_MS,
  WS_MAX_MESSAGE_SIZE,
  WS_MAX_RECONNECT_ATTEMPTS,
  WS_PONG_TIMEOUT_MS,
  WS_PROTOCOL_VERSION,
  WS_RECONNECT_BASE_DELAY,
  WS_RECONNECT_JITTER_MAX,
  WS_RECONNECT_JITTER_MIN,
  WS_RECONNECT_MAX_DELAY,
} from '@/utils/constants'
import { sanitizeForLog } from '@/utils/logging'
import { asObjectRecord } from '@/utils/parse'
import { createLogger } from '@/lib/logger'

const log = createLogger('ws')

/** Build a stable deduplication key for a subscription (sorted channels + sorted filter keys). */
function subscriptionKey(channels: WsChannel[], filters?: Record<string, string>): string {
  const sortedChannels = [...channels].sort()
  const sortedFilters: Record<string, string> = {}
  if (filters) {
    for (const key of Object.keys(filters).sort()) {
      sortedFilters[key] = filters[key]!
    }
  }
  return JSON.stringify({ channels: sortedChannels, filters: sortedFilters })
}

// ── Module-scoped internals (not renderable state) ──────────

let socket: WebSocket | null = null
let reconnectAttempts = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let heartbeatTimer: ReturnType<typeof setInterval> | null = null
let pongTimer: ReturnType<typeof setTimeout> | null = null
let intentionalClose = false
let shouldBeConnected = false
let connectPromise: Promise<void> | null = null
let connectGeneration = 0
const channelHandlers = new Map<string, Set<WsEventHandler>>()
let pendingSubscriptions: { channels: WsChannel[]; filters?: Record<string, string> }[] = []
const activeSubscriptions: { channels: WsChannel[]; filters?: Record<string, string> }[] = []

// SSE fallback transport bookkeeping. When the WS handshake fails
// twice in a row with a 1006 close (proxy-blocked WS upgrade is the
// canonical failure mode), the store switches to a read-only SSE
// feed against `/api/v1/events/stream`. The fallback dispatches
// AG-UI projected events through the same `dispatchEvent` handler
// chain so the dashboard's tasks / agents / approvals / budget
// stores keep updating; write-path features rely on the
// `connection.limited` toast to direct the user.
let sseClient: { close: () => void } | null = null
let proxyBlockSuspicion = 0
const PROXY_BLOCK_THRESHOLD = 2
const WS_ABNORMAL_CLOSE_CODE = 1006

// ── Store types ─────────────────────────────────────────────

interface WebSocketState {
  connected: boolean
  reconnectExhausted: boolean
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

// ── Helpers ─────────────────────────────────────────────────

/** Known valid WsChannel values for runtime validation (derived from types.ts). */
const VALID_WS_CHANNELS: ReadonlySet<string> = new Set(WS_CHANNELS)

/** WS close codes that indicate auth failure (do not reconnect). */
const WS_AUTH_FAILURE_CODES = new Set([4001, 4003])

function getWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}/api/v1/ws`
}

/** Runtime validation that a parsed message conforms to the WsEvent shape. */
function isWsEvent(msg: Record<string, unknown>): msg is Record<string, unknown> & WsEvent {
  return (
    typeof msg.event_type === 'string' &&
    typeof msg.channel === 'string' &&
    typeof msg.timestamp === 'string' &&
    typeof msg.payload === 'object' &&
    msg.payload !== null &&
    !Array.isArray(msg.payload)
  )
}

/**
 * Resolve the wire-protocol version of an incoming event. Absent
 * ``version`` is treated as ``1`` for backwards compatibility with
 * pre-versioning servers.
 */
function eventVersion(msg: Record<string, unknown>): number {
  return typeof msg.version === 'number' ? msg.version : 1
}

/** Validate that a channels array from a server ack contains only known channel strings. */
function isWsChannelArray(arr: unknown): arr is WsChannel[] {
  return Array.isArray(arr) && arr.every((c) => typeof c === 'string' && VALID_WS_CHANNELS.has(c))
}

/** Estimate byte length of a string (accounts for multi-byte characters). */
function estimateByteLength(str: string): number {
  // TextEncoder gives accurate UTF-8 byte count
  return new TextEncoder().encode(str).byteLength
}

function dispatchEvent(event: WsEvent) {
  channelHandlers.get(event.channel)?.forEach((h) => {
    try { h(event) } catch (err) {
      log.error('Channel handler error:', err)
    }
  })
  channelHandlers.get('*')?.forEach((h) => {
    try { h(event) } catch (err) {
      log.error('Wildcard handler error:', err)
    }
  })
}

// ── Store ───────────────────────────────────────────────────

/**
 * Stop any in-flight heartbeat / pong-timeout timers. Idempotent and
 * safe to call from any teardown path (reconnect, disconnect, close).
 */
function stopHeartbeat() {
  if (heartbeatTimer) {
    clearInterval(heartbeatTimer)
    heartbeatTimer = null
  }
  if (pongTimer) {
    clearTimeout(pongTimer)
    pongTimer = null
  }
}

/**
 * Begin sending pings every {@link WS_HEARTBEAT_INTERVAL_MS}. Each
 * ping arms a {@link WS_PONG_TIMEOUT_MS} timer; if the matching pong
 * doesn't arrive in time the socket is closed which triggers the
 * normal reconnect path.
 *
 * The heartbeat is bound to a specific socket so a stale generation
 * cannot survive a reconnect.
 */
function startHeartbeat(target: WebSocket) {
  stopHeartbeat()
  heartbeatTimer = setInterval(() => {
    if (socket !== target || target.readyState !== WebSocket.OPEN) {
      stopHeartbeat()
      return
    }
    try {
      target.send(JSON.stringify({ action: 'ping' }))
    } catch (err) {
      log.warn('Heartbeat ping send failed:', err)
      target.close()
      return
    }
    if (pongTimer) clearTimeout(pongTimer)
    pongTimer = setTimeout(() => {
      log.warn('Pong timeout reached, closing socket to trigger reconnect')
      pongTimer = null
      if (socket === target) {
        target.close()
      }
    }, WS_PONG_TIMEOUT_MS)
  }, WS_HEARTBEAT_INTERVAL_MS)
}

function queueSubscriptionForReconnect(
  channels: WsChannel[],
  filters: WsSubscriptionFilters | undefined,
  key: string,
): void {
  if (!pendingSubscriptions.some((s) => subscriptionKey(s.channels, s.filters) === key)) {
    pendingSubscriptions.push({ channels, filters })
  }
}

function activateSseFallback(): void {
  if (sseClient !== null) return
  log.warn('WS handshake repeatedly failed with 1006; activating SSE fallback')
  // Stop attempting to reconnect WS while the fallback runs; the
  // user has to reload to retry the WS transport (the reload also
  // resets ``proxyBlockSuspicion``). Without this guard, the
  // backoff loop would keep cycling through 1006 closes while the
  // SSE feed already covers the read surface.
  shouldBeConnected = false
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  sseClient = openSseFallback({
    onOpen: () => {
      log.debug('SSE fallback connected')
    },
    onEvent: (wsEvent) => {
      dispatchEvent(wsEvent)
    },
    onError: (err) => {
      log.warn('SSE fallback transport error', sanitizeForLog(err.message))
    },
  })
  void notifyConnectionLimited()
}

async function notifyConnectionLimited(): Promise<void> {
  // Lazy-import the toast store so the websocket module does not
  // pull in the entire notifications surface during cold start;
  // the dynamic import also keeps the test harness's stubbing path
  // (vi.mock) simpler.
  try {
    const { useToastStore } = await import('@/stores/toast')
    useToastStore.getState().add({
      variant: 'warning',
      title: 'Connection limited',
      description: 'Real-time WebSocket is blocked. Falling back to SSE; some interactive features (chat, settings actions) may be unavailable until you reload after fixing your proxy.',
    })
  } catch (err) {
    log.warn('Could not surface connection-limited toast', sanitizeForLog(err))
    // Fallback signal: even when the toast store is unavailable
    // (cold-boot or test harness without the notifications surface),
    // operators inspecting the console still see the limited-
    // connection state so chat/settings failures aren't a mystery.
    log.warn(
      'SSE fallback active; chat and settings features unavailable until reload',
    )
  }
}

export const useWebSocketStore = create<WebSocketState>()((set) => {
  function scheduleReconnect() {
    if (reconnectTimer) clearTimeout(reconnectTimer)
    if (reconnectAttempts >= WS_MAX_RECONNECT_ATTEMPTS) {
      log.error('Max reconnection attempts reached')
      set({ reconnectExhausted: true })
      return
    }
    const baseDelay = Math.min(
      WS_RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts),
      WS_RECONNECT_MAX_DELAY,
    )
    // Apply +/-20% randomised jitter so a server-restart-driven
    // reconnect storm de-correlates across clients instead of all
    // clients hammering the gateway in lockstep on every backoff
    // tick. Range comes from the ``WS_RECONNECT_JITTER_*`` ratios
    // declared in ``utils/constants`` so the value is greppable and
    // testable from a single source.
    const jitterMultiplier =
      WS_RECONNECT_JITTER_MIN +
      Math.random() * (WS_RECONNECT_JITTER_MAX - WS_RECONNECT_JITTER_MIN)
    // Clamp the post-rounding result to ``[1ms, WS_RECONNECT_MAX_DELAY]``.
    // The 1ms floor stops a future tuning of the base / jitter
    // constants that produces a sub-millisecond delay from collapsing
    // the backoff to an immediate reconnect; the max ceiling stops the
    // upper-bound jitter multiplier (1.2 today) from pushing the
    // delay past the configured max once ``baseDelay`` is already
    // saturated at ``WS_RECONNECT_MAX_DELAY``.
    const delay = Math.max(
      1,
      Math.min(
        WS_RECONNECT_MAX_DELAY,
        Math.round(baseDelay * jitterMultiplier),
      ),
    )
    reconnectAttempts++
    reconnectTimer = setTimeout(() => {
      if (shouldBeConnected) {
        useWebSocketStore.getState().connect().catch((err) => {
          log.error('Reconnect failed:', err)
        })
      }
    }, delay)
  }

  async function doConnect(generation: number) {
    set({ reconnectExhausted: false })
    shouldBeConnected = true
    intentionalClose = false

    let ticket: string
    try {
      const resp = await getWsTicket()
      ticket = resp.ticket
    } catch (err) {
      log.error('Ticket exchange failed:', err)
      const isAuthError = err instanceof AxiosError && err.response?.status === 401
      if (shouldBeConnected && !isAuthError) {
        scheduleReconnect()
      }
      throw err
    }

    // Guard against stale connect attempts
    if (!shouldBeConnected || generation !== connectGeneration) {
      return
    }

    // First-message auth: connect without ticket in URL, send it as first message
    const url = getWsUrl()
    const thisSocket = new WebSocket(url)
    socket = thisSocket

    thisSocket.onopen = () => {
      // Guard: if a newer connection replaced us, bail out
      if (socket !== thisSocket) return

      // Send auth ticket as first message (keeps ticket out of URL/logs/history).
      // ``connected`` deliberately stays ``false`` until the server confirms
      // the ticket via ``{ action: "auth_ok" }`` -- this closes the
      // pre-existing flash where the UI announced connectivity before the
      // server had validated the ticket.
      try {
        thisSocket.send(JSON.stringify({ action: 'auth', ticket }))
      } catch (err) {
        log.error('Auth send failed:', err)
        thisSocket.close()
        return
      }

      // Replay any active subscriptions. The server processes them after
      // auth completes, so the order on the wire is auth -> subscribe(s),
      // and the server's auth_ok frame can land before or after the
      // subscribe ack -- both orderings are safe.
      pendingSubscriptions = []
      for (const sub of activeSubscriptions) {
        try {
          thisSocket.send(JSON.stringify({ action: 'subscribe', channels: sub.channels, filters: sub.filters }))
        } catch (err) {
          log.error('Subscribe send failed (will retry on reconnect):', err)
        }
      }
    }

    thisSocket.onmessage = (event: MessageEvent) => {
      if (typeof event.data !== 'string') return
      if (estimateByteLength(event.data) > WS_MAX_MESSAGE_SIZE) {
        log.error('Message exceeds max size, discarding')
        return
      }
      let data: unknown
      try {
        data = JSON.parse(event.data)
      } catch (parseErr) {
        log.error('Failed to parse message:', parseErr)
        return
      }

      const msg = asObjectRecord(data)
      if (!msg) {
        log.error('Message is not a JSON object, discarding')
        return
      }

      if (msg.action === 'auth_ok') {
        // Server has validated the ticket. NOW we can flip connected
        // and start the heartbeat -- this closes the pre-existing flash.
        set({ connected: true })
        reconnectAttempts = 0
        // Successful handshake clears the proxy-block suspicion; if
        // a future close fires 1006 it must be a fresh transport
        // failure, not the same misconfigured upgrade path repeating.
        proxyBlockSuspicion = 0
        startHeartbeat(thisSocket)
        return
      }

      if (msg.action === 'pong') {
        if (pongTimer) {
          clearTimeout(pongTimer)
          pongTimer = null
        }
        return
      }

      if (msg.action === 'subscribed' || msg.action === 'unsubscribed') {
        if (isWsChannelArray(msg.channels)) {
          set({ subscribedChannels: [...msg.channels] })
        }
        return
      }

      if (msg.error) {
        // Truncate attacker-controlled error value for log injection mitigation
        log.error('Server error:', sanitizeForLog(msg.error, LOG_SANITIZE_MAX_LENGTH))
        return
      }

      if (isWsEvent(msg)) {
        const version = eventVersion(msg)
        if (version !== WS_PROTOCOL_VERSION) {
          log.warn('Discarding event with unsupported wire version:', {
            received: version,
            supported: WS_PROTOCOL_VERSION,
            // event_type + channel are attacker-reachable via the
            // WS payload; scrub before embedding in the log to close
            // the log-injection vector.
            event_type: sanitizeForLog(msg.event_type),
            channel: sanitizeForLog(msg.channel),
          })
          return
        }
        dispatchEvent(msg)
      } else {
        log.warn('Message failed WsEvent validation, discarding:', {
          hasEventType: typeof msg.event_type,
          hasChannel: typeof msg.channel,
          hasTimestamp: typeof msg.timestamp,
          hasPayload: typeof msg.payload,
        })
      }
    }

    thisSocket.onclose = (event: CloseEvent) => {
      // Guard: only act on our own socket, not a stale reference
      if (socket !== thisSocket) return
      const wasConnected = useWebSocketStore.getState().connected
      stopHeartbeat()
      set({ connected: false })
      socket = null

      // Auth failures (4001/4003): do not reconnect -- surface error
      if (WS_AUTH_FAILURE_CODES.has(event.code)) {
        log.error(`Auth failed (code ${event.code}):`, sanitizeForLog(event.reason, LOG_SANITIZE_MAX_LENGTH))
        set({ reconnectExhausted: true })
        return
      }

      // Proxy-blocked WS detection: a 1006 close that fires BEFORE
      // ``auth_ok`` ever landed (``wasConnected === false``) is the
      // canonical signature of a reverse proxy that does not forward
      // WS upgrades. Count consecutive occurrences; once over
      // PROXY_BLOCK_THRESHOLD, give up on the WS transport and open
      // the SSE fallback. The read-only AG-UI projection keeps the
      // tasks / approvals / agents / budget surfaces live; write-
      // path features rely on the `connection.limited` toast to
      // direct the operator.
      if (event.code === WS_ABNORMAL_CLOSE_CODE && !wasConnected) {
        proxyBlockSuspicion += 1
        if (proxyBlockSuspicion >= PROXY_BLOCK_THRESHOLD && sseClient === null) {
          activateSseFallback()
          return
        }
      } else if (wasConnected) {
        proxyBlockSuspicion = 0
      }

      if (!intentionalClose && shouldBeConnected) {
        scheduleReconnect()
      }
    }

    thisSocket.onerror = () => {
      log.error('Connection error', {
        url,
        readyState: thisSocket.readyState,
        reconnectAttempts,
      })
    }
  }

  return {
    connected: false,
    reconnectExhausted: false,
    subscribedChannels: [],

    async connect() {
      if (connectPromise) return connectPromise
      if (socket?.readyState === WebSocket.OPEN || socket?.readyState === WebSocket.CONNECTING) return
      const generation = connectGeneration
      connectPromise = doConnect(generation).finally(() => {
        if (generation === connectGeneration) connectPromise = null
      })
      return connectPromise
    },

    disconnect() {
      intentionalClose = true
      shouldBeConnected = false
      connectGeneration++
      connectPromise = null
      reconnectAttempts = 0
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      stopHeartbeat()
      if (socket) {
        socket.close()
        socket = null
      }
      if (sseClient) {
        sseClient.close()
        sseClient = null
      }
      proxyBlockSuspicion = 0
      set({ connected: false, subscribedChannels: [] })
      pendingSubscriptions = []
      activeSubscriptions.length = 0
      channelHandlers.clear()
    },

    async retry() {
      // Wired to the "Retry" action surfaced on reconnect-exhausted
      // toasts and badges. Resets the failure budget and asks the
      // store to attempt a fresh connect; the regular reconnect /
      // auth_ok / heartbeat path takes over from there.
      reconnectAttempts = 0
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      set({ reconnectExhausted: false })
      await useWebSocketStore.getState().connect()
    },

    subscribe(channels: WsChannel[], filters?: WsSubscriptionFilters) {
      const key = subscriptionKey(channels, filters)
      if (!activeSubscriptions.some((s) => subscriptionKey(s.channels, s.filters) === key)) {
        activeSubscriptions.push({ channels: [...channels], filters: filters ? { ...filters } : undefined })
      }

      if (!socket || socket.readyState !== WebSocket.OPEN) {
        if (!pendingSubscriptions.some((s) => subscriptionKey(s.channels, s.filters) === key)) {
          pendingSubscriptions.push({ channels, filters })
        }
        return
      }
      const frame = JSON.stringify({ action: 'subscribe', channels, filters })
      try {
        socket.send(frame)
      } catch (err) {
        // D1: a transient send failure (e.g. an instant of socket
        // back-pressure) used to drop straight into the reconnect
        // queue, stranding the subscription for tens of seconds until
        // the auth_ok handshake replayed it. Schedule one immediate
        // microtask retry against the same socket so a single
        // failure does not silently disable the channel; on the
        // second failure (or if the socket has moved out of OPEN),
        // fall through to the queue-for-reconnect path.
        log.warn('Subscribe send failed, retrying on next microtask:', sanitizeForLog(err))
        const retrySocket = socket
        queueMicrotask(() => {
          if (retrySocket !== socket) return
          if (retrySocket.readyState !== WebSocket.OPEN) {
            queueSubscriptionForReconnect(channels, filters, key)
            return
          }
          try {
            retrySocket.send(frame)
          } catch (retryErr) {
            log.error('Subscribe send retry failed, queued for reconnect:', sanitizeForLog(retryErr))
            queueSubscriptionForReconnect(channels, filters, key)
          }
        })
      }
    },

    unsubscribe(channels: WsChannel[]) {
      const channelSet = new Set(channels)
      // Remove matching channels from stored subscriptions and clean up empty entries
      for (let i = activeSubscriptions.length - 1; i >= 0; i--) {
        activeSubscriptions[i]!.channels = activeSubscriptions[i]!.channels.filter((c) => !channelSet.has(c))
        if (activeSubscriptions[i]!.channels.length === 0) {
          activeSubscriptions.splice(i, 1)
        }
      }
      for (let i = pendingSubscriptions.length - 1; i >= 0; i--) {
        pendingSubscriptions[i]!.channels = pendingSubscriptions[i]!.channels.filter((c) => !channelSet.has(c))
        if (pendingSubscriptions[i]!.channels.length === 0) {
          pendingSubscriptions.splice(i, 1)
        }
      }

      if (!socket || socket.readyState !== WebSocket.OPEN) return
      try {
        socket.send(JSON.stringify({ action: 'unsubscribe', channels }))
      } catch (err) {
        log.error('Unsubscribe send failed:', err)
      }
    },

    onChannelEvent(channel: WsChannel | '*', handler: WsEventHandler) {
      if (!channelHandlers.has(channel)) {
        channelHandlers.set(channel, new Set())
      }
      channelHandlers.get(channel)!.add(handler)
    },

    offChannelEvent(channel: WsChannel | '*', handler: WsEventHandler) {
      channelHandlers.get(channel)?.delete(handler)
    },

    rollbackSubscriptions(
      channels: readonly WsChannel[],
      bindings: readonly { channel: WsChannel; handler: WsEventHandler }[],
      options?: { unsubscribe?: boolean },
    ) {
      // Best-effort teardown. Each leg is independently safe:
      // ``offChannelEvent`` is a Map/Set delete (cannot throw) and
      // ``unsubscribe`` swallows its own send failures via ``log.error``.
      // A ``try``/``catch`` around each leg defends against future
      // store actions that may throw without forcing callers (the hook)
      // to own store error UX.
      const self = useWebSocketStore.getState()
      for (const binding of bindings) {
        try {
          self.offChannelEvent(binding.channel, binding.handler)
        } catch (err) {
          log.error('rollbackSubscriptions: offChannelEvent failed:', err)
        }
      }
      if (options?.unsubscribe !== false && channels.length > 0) {
        // Only unsubscribe channels that no longer have any handler
        // registrations. Multiple ``useWebSocket`` hooks can share a
        // channel, so an unmount that blindly unsubscribes every
        // channel in its own binding set would cut off broadcast
        // traffic for sibling hooks that are still mounted. Consult
        // the module-scope ``channelHandlers`` map after the per-
        // binding ``offChannelEvent`` calls above have pruned this
        // hook's own entries; any channel with a non-empty Set is
        // still in use by another subscriber.
        const channelsToUnsubscribe = [...new Set(channels)].filter(
          (channel) => (channelHandlers.get(channel)?.size ?? 0) === 0,
        )
        if (channelsToUnsubscribe.length > 0) {
          try {
            self.unsubscribe(channelsToUnsubscribe)
          } catch (err) {
            log.error('rollbackSubscriptions: unsubscribe failed:', err)
          }
        }
      }
    },

    teardown() {
      // Step 1: mark the connection as intentionally torn down so any
      // in-flight ``onclose`` handler does not re-arm
      // ``scheduleReconnect`` while we are still resetting.
      intentionalClose = true
      shouldBeConnected = false
      connectGeneration++
      connectPromise = null
      reconnectAttempts = 0
      proxyBlockSuspicion = 0
      if (sseClient) {
        sseClient.close()
        sseClient = null
      }
      // Step 2: clear every module-scope timer handle. ``stopHeartbeat``
      // covers the heartbeat + pong pair; the reconnect timer is
      // separate.
      if (reconnectTimer) {
        clearTimeout(reconnectTimer)
        reconnectTimer = null
      }
      stopHeartbeat()
      // Step 3: detach event handlers BEFORE close so a synchronous
      // ``onclose`` in MockWebSocket (or any other implementation that
      // fires close synchronously) cannot run with stale module state
      // and accidentally reschedule reconnects via ``scheduleReconnect``.
      if (socket) {
        socket.onopen = null
        socket.onclose = null
        socket.onerror = null
        socket.onmessage = null
        try {
          socket.close()
        } catch {
          // Best-effort: a half-closed mock or a socket that already
          // raised on construction must not block the teardown.
        }
        socket = null
      }
      // Step 4: drop subscription bookkeeping + handler registrations
      // so the next test starts with empty maps and arrays.
      pendingSubscriptions = []
      activeSubscriptions.length = 0
      channelHandlers.clear()
      // Step 5: reset observable store state. Unlike ``disconnect()``,
      // teardown also clears ``reconnectExhausted`` so a previous test
      // that exhausted the reconnect budget cannot leak that flag into
      // a fresh test.
      set({ connected: false, reconnectExhausted: false, subscribedChannels: [] })
    },
  }
})

// Vite Fast Refresh dispose hook: tear down sockets, timers, and event
// handlers when this module is replaced in dev. Without this, an HMR
// swap leaves the previous module's interval / reconnect chain armed
// against the previous socket, causing duplicate writers and ghost
// reconnects in the dev server. Mirrors the theme store's
// ``import.meta.hot?.dispose(...)`` per ``web/CLAUDE.md`` ("Test
// teardown").
if (import.meta.hot) {
  import.meta.hot.dispose(() => {
    useWebSocketStore.getState().teardown()
  })
}

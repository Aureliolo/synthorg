import { AxiosError } from 'axios'
import { getWsTicket } from '@/api/endpoints/auth'
import {
  LOG_SANITIZE_MAX_LENGTH,
  WS_HEARTBEAT_INTERVAL_MS,
  WS_HEARTBEAT_JITTER_MAX,
  WS_HEARTBEAT_JITTER_MIN,
  WS_MAX_MESSAGE_SIZE,
  WS_MAX_RECONNECT_ATTEMPTS,
  WS_PONG_TIMEOUT_MS,
  WS_RECONNECT_BASE_DELAY,
  WS_RECONNECT_JITTER_MAX,
  WS_RECONNECT_JITTER_MIN,
  WS_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'
import { sanitizeForLog } from '@/utils/logging'
import { asObjectRecord } from '@/utils/parse'
import { createLogger } from '@/lib/logger'
import {
  estimateByteLength,
  eventVersion,
  isWsChannelArray,
  isWsEvent,
} from './dispatch'
import {
  dispatchEvent,
  replaySubscriptions,
  teardownSubscriptions,
} from './subscriptions'
import {
  activateSseFallback,
  closeSseFallback,
  isSseFallbackActive,
  recordAbnormalCloseDuringHandshake,
  resetProxyBlockSuspicion,
} from './sse-fallback'
import { isSupportedWireVersion, resetProtocolMismatchCount } from './protocol-guard'
import { getCurrentSocket, setCurrentSocket } from './transport-shared'
import type { WsGet, WsSet } from './types'

const log = createLogger('ws')

let reconnectAttempts = 0
let reconnectTimer: ReturnType<typeof setTimeout> | null = null
let heartbeatTimer: ReturnType<typeof setTimeout> | null = null
let pongTimer: ReturnType<typeof setTimeout> | null = null
let intentionalClose = false
let shouldBeConnected = false
let connectPromise: Promise<void> | null = null
let connectGeneration = 0

const WS_ABNORMAL_CLOSE_CODE = 1006
/** WS close codes that indicate auth failure (do not reconnect). */
const WS_AUTH_FAILURE_CODES = new Set([4001, 4003])

function getWsUrl(): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const host = window.location.host
  return `${protocol}//${host}/api/v1/ws`
}

/**
 * Pick a heartbeat delay in
 * ``WS_HEARTBEAT_INTERVAL_MS * [WS_HEARTBEAT_JITTER_MIN, WS_HEARTBEAT_JITTER_MAX]``
 * so a fleet of long-lived dashboards does not ping the server in
 * lockstep.
 */
function jitteredHeartbeatDelay(): number {
  const span = WS_HEARTBEAT_JITTER_MAX - WS_HEARTBEAT_JITTER_MIN
  const factor = WS_HEARTBEAT_JITTER_MIN + Math.random() * span
  return WS_HEARTBEAT_INTERVAL_MS * factor
}

/**
 * Stop any in-flight heartbeat / pong-timeout timers. Idempotent and
 * safe to call from any teardown path (reconnect, disconnect, close).
 */
function stopHeartbeat(): void {
  if (heartbeatTimer) {
    clearTimeout(heartbeatTimer)
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
function startHeartbeat(target: WebSocket): void {
  stopHeartbeat()
  const tick = () => {
    if (getCurrentSocket() !== target || target.readyState !== WebSocket.OPEN) {
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
      if (getCurrentSocket() === target) {
        target.close()
      }
    }, WS_PONG_TIMEOUT_MS)
    heartbeatTimer = setTimeout(tick, jitteredHeartbeatDelay())
  }
  heartbeatTimer = setTimeout(tick, jitteredHeartbeatDelay())
}

function computeReconnectDelay(): number {
  const baseDelay = Math.min(
    WS_RECONNECT_BASE_DELAY * Math.pow(2, reconnectAttempts),
    WS_RECONNECT_MAX_DELAY,
  )
  // +/-20% jitter (the ``WS_RECONNECT_JITTER_*`` ratios) de-correlates a
  // server-restart-driven reconnect storm across clients.
  const jitterMultiplier =
    WS_RECONNECT_JITTER_MIN
    + Math.random() * (WS_RECONNECT_JITTER_MAX - WS_RECONNECT_JITTER_MIN)
  // Clamp to ``[1ms, WS_RECONNECT_MAX_DELAY]``: the floor stops a sub-ms
  // delay collapsing the backoff to an immediate reconnect; the ceiling
  // stops the upper jitter multiplier exceeding the max once ``baseDelay``
  // is already saturated.
  return Math.max(
    1,
    Math.min(
      WS_RECONNECT_MAX_DELAY,
      Math.round(baseDelay * jitterMultiplier),
    ),
  )
}

function scheduleReconnect(set: WsSet, get: WsGet): void {
  if (reconnectTimer) clearTimeout(reconnectTimer)
  if (reconnectAttempts >= WS_MAX_RECONNECT_ATTEMPTS) {
    log.error('Max reconnection attempts reached')
    set({ reconnectExhausted: true })
    return
  }
  const delay = computeReconnectDelay()
  reconnectAttempts++
  reconnectTimer = setTimeout(() => {
    if (shouldBeConnected) {
      void connectImpl(set, get).catch((err: unknown) => {
        log.error('Reconnect failed:', err)
      })
    }
  }, delay)
}

function handleAuthOk(thisSocket: WebSocket, set: WsSet): void {
  // Tear down the SSE fallback only once the replacement WS is proven
  // usable (authenticated). Doing it here rather than before the ticket
  // exchange keeps the fallback live through a failed handshake, so a
  // dropped ticket / never-reached ``auth_ok`` does not leave the
  // dashboard with neither transport. The WS does not dispatch events
  // before ``auth_ok``, so there is no double-fire window to close.
  closeSseFallback(set)
  set({ connected: true })
  reconnectAttempts = 0
  // Successful handshake clears the proxy-block suspicion; if
  // a future close fires 1006 it must be a fresh transport
  // failure, not the same misconfigured upgrade path repeating.
  resetProxyBlockSuspicion()
  startHeartbeat(thisSocket)
}

function handleAck(msg: Record<string, unknown>, set: WsSet): void {
  if (isWsChannelArray(msg['channels'])) {
    set({ subscribedChannels: [...msg['channels']] })
  }
}

function handleEventOrLog(msg: Record<string, unknown>, set: WsSet): void {
  if (!isWsEvent(msg)) {
    log.warn('Message failed WsEvent validation, discarding:', {
      hasEventType: typeof msg['event_type'],
      hasChannel: typeof msg['channel'],
      hasTimestamp: typeof msg['timestamp'],
      hasPayload: typeof msg['payload'],
    })
    return
  }
  if (!isSupportedWireVersion(eventVersion(msg), msg, set)) return
  dispatchEvent(msg)
}

function routeIncomingMessage(msg: Record<string, unknown>, set: WsSet): void {
  if (msg['action'] === 'pong') {
    if (pongTimer) {
      clearTimeout(pongTimer)
      pongTimer = null
    }
    return
  }
  if (msg['action'] === 'subscribed' || msg['action'] === 'unsubscribed') {
    handleAck(msg, set)
    return
  }
  if (msg['error']) {
    log.error(
      'Server error:',
      sanitizeForLog(msg['error'], LOG_SANITIZE_MAX_LENGTH),
    )
    return
  }
  handleEventOrLog(msg, set)
}

function shouldFallbackToSse(event: CloseEvent, wasConnected: boolean): boolean {
  // Proxy-blocked WS detection: a 1006 close that fires BEFORE
  // ``auth_ok`` ever landed (``wasConnected === false``) is the
  // canonical signature of a reverse proxy that does not forward
  // WS upgrades. Count consecutive occurrences; once over the
  // SSE fallback threshold, give up on the WS transport and open
  // the SSE fallback. The read-only AG-UI projection keeps the
  // tasks / approvals / agents / budget surfaces live; write-
  // path features rely on the ``connection.limited`` toast to
  // direct the operator.
  if (event.code !== WS_ABNORMAL_CLOSE_CODE || wasConnected) {
    resetProxyBlockSuspicion()
    return false
  }
  return recordAbnormalCloseDuringHandshake() && !isSseFallbackActive()
}

function activateFallbackAndStopReconnect(set: WsSet): void {
  shouldBeConnected = false
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  activateSseFallback(set)
}

function handleClose(
  thisSocket: WebSocket,
  event: CloseEvent,
  set: WsSet,
  get: WsGet,
): void {
  if (getCurrentSocket() !== thisSocket) return
  const wasConnected = get().connected
  stopHeartbeat()
  set({ connected: false })
  setCurrentSocket(null)

  if (WS_AUTH_FAILURE_CODES.has(event.code)) {
    log.error(
      `Auth failed (code ${event.code}):`,
      sanitizeForLog(event.reason, LOG_SANITIZE_MAX_LENGTH),
    )
    set({ reconnectExhausted: true })
    return
  }
  if (shouldFallbackToSse(event, wasConnected)) {
    activateFallbackAndStopReconnect(set)
    return
  }
  if (!intentionalClose && shouldBeConnected) {
    scheduleReconnect(set, get)
  }
}

function sendAuthFrame(thisSocket: WebSocket, ticket: string): boolean {
  // Send auth ticket as first message (keeps ticket out of URL/logs/history).
  // ``connected`` deliberately stays ``false`` until the server confirms
  // the ticket via ``{ action: "auth_ok" }`` -- this closes the
  // pre-existing flash where the UI announced connectivity before the
  // server had validated the ticket.
  try {
    thisSocket.send(JSON.stringify({ action: 'auth', ticket }))
    return true
  } catch (err) {
    log.error('Auth send failed:', err)
    thisSocket.close()
    return false
  }
}

function parseIncomingFrame(rawData: unknown): Record<string, unknown> | null {
  if (typeof rawData !== 'string') return null
  if (estimateByteLength(rawData) > WS_MAX_MESSAGE_SIZE) {
    log.error('Message exceeds max size, discarding')
    return null
  }
  let data: unknown
  try {
    data = JSON.parse(rawData)
  } catch (parseErr) {
    log.error('Failed to parse message:', parseErr)
    return null
  }
  const msg = asObjectRecord(data)
  if (!msg) {
    log.error('Message is not a JSON object, discarding')
    return null
  }
  return msg
}

function wireSocketHandlers(
  thisSocket: WebSocket,
  ticket: string,
  url: string,
  set: WsSet,
  get: WsGet,
): void {
  thisSocket.onopen = () => {
    if (getCurrentSocket() !== thisSocket) return
    if (!sendAuthFrame(thisSocket, ticket)) return
    replaySubscriptions(thisSocket)
  }
  thisSocket.onmessage = (event: MessageEvent) => {
    const msg = parseIncomingFrame(event.data)
    if (!msg) return
    if (msg['action'] === 'auth_ok') {
      handleAuthOk(thisSocket, set)
      return
    }
    routeIncomingMessage(msg, set)
  }
  thisSocket.onclose = (event: CloseEvent) => {
    handleClose(thisSocket, event, set, get)
  }
  thisSocket.onerror = () => {
    log.error('Connection error', {
      url,
      readyState: thisSocket.readyState,
      reconnectAttempts,
    })
  }
}

async function fetchTicketOrReconnect(
  set: WsSet,
  get: WsGet,
): Promise<string | null> {
  try {
    const resp = await getWsTicket()
    return resp.ticket
  } catch (err) {
    log.error('Ticket exchange failed:', err)
    const isAuthError = err instanceof AxiosError
      && err.response?.status === 401
    if (shouldBeConnected && !isAuthError) {
      scheduleReconnect(set, get)
    }
    throw err
  }
}

async function doConnect(
  generation: number,
  set: WsSet,
  get: WsGet,
): Promise<void> {
  set({ reconnectExhausted: false, protocolVersionMismatch: false })
  resetProtocolMismatchCount()
  shouldBeConnected = true
  intentionalClose = false

  const ticket = await fetchTicketOrReconnect(set, get)
  if (ticket === null) return
  // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- set false by disconnect() during the await; CFA cannot see the cross-function mutation
  if (!shouldBeConnected || generation !== connectGeneration) return

  const url = getWsUrl()
  const thisSocket = new WebSocket(url)
  setCurrentSocket(thisSocket)
  wireSocketHandlers(thisSocket, ticket, url, set, get)
}

async function connectImpl(set: WsSet, get: WsGet): Promise<void> {
  if (connectPromise) return connectPromise
  const socket = getCurrentSocket()
  if (
    socket?.readyState === WebSocket.OPEN
    || socket?.readyState === WebSocket.CONNECTING
  ) {
    return
  }
  const generation = connectGeneration
  connectPromise = doConnect(generation, set, get).finally(() => {
    if (generation === connectGeneration) connectPromise = null
  })
  return connectPromise
}

/**
 * Reset the module-level reconnect bookkeeping shared by every
 * intentional teardown (``disconnect`` and ``teardownTransport``):
 * stop reconnecting, bump the generation so any in-flight ``doConnect``
 * bails, and clear the failure / mismatch / proxy-block counters and
 * timers. Socket close and store updates are left to the caller since
 * they differ between the two paths.
 */
function _resetReconnectState(): void {
  intentionalClose = true
  shouldBeConnected = false
  connectGeneration++
  connectPromise = null
  reconnectAttempts = 0
  resetProtocolMismatchCount()
  resetProxyBlockSuspicion()
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  stopHeartbeat()
}

function disconnectImpl(set: WsSet): void {
  _resetReconnectState()
  const socket = getCurrentSocket()
  if (socket) {
    socket.close()
    setCurrentSocket(null)
  }
  closeSseFallback(set)
  set({ connected: false, subscribedChannels: [], protocolVersionMismatch: false })
  teardownSubscriptions()
}

async function retryImpl(set: WsSet, get: WsGet): Promise<void> {
  // Wired to the "Retry" action on reconnect-exhausted toasts / badges.
  // Resets the failure budget and asks the store to attempt a fresh
  // connect; the regular reconnect / auth_ok / heartbeat path takes over.
  reconnectAttempts = 0
  resetProtocolMismatchCount()
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  set({ reconnectExhausted: false, protocolVersionMismatch: false })
  // A manual retry whose ticket exchange fails (e.g. auth error) would
  // reject unhandled with reconnectExhausted cleared; re-arm it so the
  // Retry affordance stays visible.
  try {
    await connectImpl(set, get)
  } catch (err) {
    log.error('Manual retry failed:', sanitizeForLog(err))
    set({ reconnectExhausted: true })
  }
}

export function teardownTransport(): void {
  _resetReconnectState()
  closeSseFallback()
  const socket = getCurrentSocket()
  if (socket) {
    // Detach all handlers before close so a mock / half-closed socket
    // cannot fire callbacks after teardown.
    socket.onopen = socket.onclose = socket.onerror = socket.onmessage = null
    try {
      socket.close()
    } catch (err) {
      // Best-effort: a half-closed / construction-failed socket must not
      // block teardown, but log so a genuine close failure is not swallowed.
      log.warn('socket.close() threw during teardown', sanitizeForLog(err))
    }
    setCurrentSocket(null)
  }
}

export function createTransportSlice(set: WsSet, get: WsGet) {
  return {
    connect: () => connectImpl(set, get),
    disconnect: () => disconnectImpl(set),
    retry: () => retryImpl(set, get),
  }
}

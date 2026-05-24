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
  WS_PROTOCOL_VERSION,
  WS_RECONNECT_BASE_DELAY,
  WS_RECONNECT_JITTER_MAX,
  WS_RECONNECT_JITTER_MIN,
  WS_RECONNECT_MAX_DELAY,
} from '@/utils/constants'
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
  // Apply +/-20% randomised jitter so a server-restart-driven
  // reconnect storm de-correlates across clients instead of all
  // clients hammering the gateway in lockstep on every backoff
  // tick. Range comes from the ``WS_RECONNECT_JITTER_*`` ratios
  // declared in ``utils/constants`` so the value is greppable and
  // testable from a single source.
  const jitterMultiplier =
    WS_RECONNECT_JITTER_MIN
    + Math.random() * (WS_RECONNECT_JITTER_MAX - WS_RECONNECT_JITTER_MIN)
  // Clamp the post-rounding result to ``[1ms, WS_RECONNECT_MAX_DELAY]``.
  // The 1ms floor stops a future tuning of the base / jitter
  // constants that produces a sub-millisecond delay from collapsing
  // the backoff to an immediate reconnect; the max ceiling stops the
  // upper-bound jitter multiplier (1.2 today) from pushing the
  // delay past the configured max once ``baseDelay`` is already
  // saturated at ``WS_RECONNECT_MAX_DELAY``.
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
      void connectImpl(set, get).catch((err) => {
        log.error('Reconnect failed:', err)
      })
    }
  }, delay)
}

function handleAuthOk(thisSocket: WebSocket, set: WsSet): void {
  set({ connected: true })
  reconnectAttempts = 0
  // Successful handshake clears the proxy-block suspicion; if
  // a future close fires 1006 it must be a fresh transport
  // failure, not the same misconfigured upgrade path repeating.
  resetProxyBlockSuspicion()
  startHeartbeat(thisSocket)
}

function handleAck(msg: Record<string, unknown>, set: WsSet): void {
  if (isWsChannelArray(msg.channels)) {
    set({ subscribedChannels: [...msg.channels] })
  }
}

function handleEventOrLog(msg: Record<string, unknown>): void {
  if (!isWsEvent(msg)) {
    log.warn('Message failed WsEvent validation, discarding:', {
      hasEventType: typeof msg.event_type,
      hasChannel: typeof msg.channel,
      hasTimestamp: typeof msg.timestamp,
      hasPayload: typeof msg.payload,
    })
    return
  }
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
}

function routeIncomingMessage(msg: Record<string, unknown>, set: WsSet): void {
  if (msg.action === 'pong') {
    if (pongTimer) {
      clearTimeout(pongTimer)
      pongTimer = null
    }
    return
  }
  if (msg.action === 'subscribed' || msg.action === 'unsubscribed') {
    handleAck(msg, set)
    return
  }
  if (msg.error) {
    log.error(
      'Server error:',
      sanitizeForLog(msg.error, LOG_SANITIZE_MAX_LENGTH),
    )
    return
  }
  handleEventOrLog(msg)
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

function activateFallbackAndStopReconnect(): void {
  shouldBeConnected = false
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  activateSseFallback()
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
    activateFallbackAndStopReconnect()
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
    if (msg.action === 'auth_ok') {
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
  set({ reconnectExhausted: false })
  shouldBeConnected = true
  intentionalClose = false

  // Close any active SSE fallback before attempting a fresh WS.
  // If the WS handshake later succeeds, the SSE client would otherwise
  // remain open and ``dispatchEvent`` would fire on every channel
  // event twice -- once from the WS frame, once from the SSE stream.
  // Tearing it down here keeps the "only one transport at a time"
  // invariant the dispatch chain assumes.
  closeSseFallback()

  const ticket = await fetchTicketOrReconnect(set, get)
  if (ticket === null) return
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

function disconnectImpl(set: WsSet): void {
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
  const socket = getCurrentSocket()
  if (socket) {
    socket.close()
    setCurrentSocket(null)
  }
  closeSseFallback()
  resetProxyBlockSuspicion()
  set({ connected: false, subscribedChannels: [] })
  teardownSubscriptions()
}

async function retryImpl(set: WsSet, get: WsGet): Promise<void> {
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
  await connectImpl(set, get)
}

export function teardownTransport(): void {
  intentionalClose = true
  shouldBeConnected = false
  connectGeneration++
  connectPromise = null
  reconnectAttempts = 0
  closeSseFallback()
  resetProxyBlockSuspicion()
  if (reconnectTimer) {
    clearTimeout(reconnectTimer)
    reconnectTimer = null
  }
  stopHeartbeat()
  const socket = getCurrentSocket()
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

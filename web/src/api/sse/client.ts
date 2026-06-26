/**
 * SSE fallback transport.
 *
 * When the WebSocket is detected as proxy-blocked (two consecutive
 * 1006 closes with no `auth_ok`), the dashboard switches to a
 * read-only SSE feed against `/api/v1/events/dashboard` so the
 * tasks / approvals / agents / budget surfaces still update in
 * real time. Write-path features (chat, settings actions) surface
 * a "connection limited" banner via the notifications store and
 * fall back to REST polling on their own pages.
 *
 * The endpoint is session-less and channel-multiplexed: it forwards
 * the same `WsEvent` payloads the WebSocket serves, one per named
 * `ws` frame, so this transport only has to parse each frame and
 * hand the raw event object to the caller, which validates and
 * dispatches it through the same pipeline as the WebSocket path.
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import {
  SSE_MAX_RECONNECT_ATTEMPTS,
  SSE_RECONNECT_BASE_DELAY,
  SSE_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'

const log = createLogger('sse-client')

const SSE_STREAM_PATH = '/api/v1/events/dashboard'

/** The single SSE event name every dashboard `WsEvent` is published under. */
const WS_FRAME_EVENT = 'ws'

interface SseClientCallbacks {
  /**
   * Invoked with the raw parsed event object for each `ws` frame. The caller
   * validates the shape (it is the same `WsEvent` the WebSocket delivers) and
   * dispatches it; this transport does not interpret the payload.
   */
  onEvent: (event: unknown) => void
  onError: (error: Error) => void
  onOpen?: () => void
  /**
   * Invoked once the SSE transport has failed `SSE_MAX_RECONNECT_ATTEMPTS`
   * times. The client closes the `EventSource` first so the caller only needs
   * to surface the exhausted state; it does not retry on its own afterwards.
   */
  onExhausted?: () => void
}

interface SseClient {
  close: () => void
}

/** Parse one SSE frame, surface its event id, and forward the raw event. */
function processSseFrame(
  event: MessageEvent,
  onEvent: (event: unknown) => void,
  onLastEventId: (id: string) => void,
): void {
  if (event.lastEventId) {
    // Clamp the server-supplied id before we store / forward it: it is
    // attacker-influenced and otherwise uncapped (control chars, bidi
    // overrides, unbounded length).
    const sanitizedId = sanitizeWsString(event.lastEventId)
    if (sanitizedId !== undefined) onLastEventId(sanitizedId)
  }
  if (typeof event.data !== 'string') return
  let parsed: unknown
  try {
    parsed = JSON.parse(event.data)
  } catch (parseErr) {
    log.warn('Failed to parse SSE frame', sanitizeForLog(parseErr))
    return
  }
  // Only forward plain objects; the caller's WsEvent validation rejects
  // anything malformed, but excluding non-objects up front keeps the log
  // signal clean and avoids handing arrays / scalars downstream.
  if (parsed === null || typeof parsed !== 'object' || Array.isArray(parsed)) {
    log.warn('SSE frame was not an event object, discarding')
    return
  }
  onEvent(parsed)
}

/** Exponential backoff (ms) for the Nth reconnect attempt, capped at MAX. */
function computeReconnectDelay(attempt: number): number {
  return Math.min(
    SSE_RECONNECT_BASE_DELAY * 2 ** (attempt - 1),
    SSE_RECONNECT_MAX_DELAY,
  )
}

/**
 * Open an SSE connection to the dashboard event feed and forward every
 * `ws` frame's parsed event to the caller.
 *
 * Reconnection is driven at the application level (close + re-`new
 * EventSource`) with exponential backoff (`SSE_RECONNECT_BASE_DELAY`
 * doubling to `SSE_RECONNECT_MAX_DELAY`), mirroring the WS transport's
 * backoff policy: the browser's native `EventSource` retry is a flat
 * cadence with no backoff, so a prolonged outage would otherwise hammer
 * the backend. Returns a handle whose `close()` cancels any pending
 * reconnect timer and tears down the EventSource.
 *
 * Replay on reconnect: because we drive reconnect ourselves (a fresh
 * `EventSource` each cycle) the browser never re-sends its native
 * `Last-Event-ID` header, so the last id we saw is threaded back as a
 * `last_event_id` query parameter instead. Its presence tells the backend
 * to replay the recent per-channel backlog so events published during the
 * outage are not silently dropped; the dispatch pipeline de-duplicates.
 */
export function openSseFallback(callbacks: SseClientCallbacks): SseClient {
  let source: EventSource | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let closed = false
  // Reconnect attempts since the last clean open; drives both the backoff
  // delay and the SSE_MAX_RECONNECT_ATTEMPTS budget. Reset in ``onopen``.
  let attempt = 0
  // Notify the caller of a disconnect only once per outage cycle so a single
  // interruption does not flood the operator with toasts; reset on re-open.
  let reportedDisconnect = false
  let lastEventId = ''
  // Wall-clock of the last successful open; 0 while disconnected. Used to
  // distinguish a stable connection (open for at least SSE_RECONNECT_MAX_DELAY)
  // from a short-lived flap so the attempt budget is only reset for the former.
  let openedAt = 0

  const handleFrame = (event: MessageEvent): void => {
    processSseFrame(event, callbacks.onEvent, (id) => {
      lastEventId = id
    })
  }

  function streamUrl(): string {
    if (!lastEventId) return SSE_STREAM_PATH
    return `${SSE_STREAM_PATH}?last_event_id=${encodeURIComponent(lastEventId)}`
  }

  // Null handlers before closing so closure captures release promptly; some
  // engines do not free EventSource handlers on .close() alone.
  function detachSource(): void {
    if (!source) return
    source.onopen = null
    source.onerror = null
    source.removeEventListener(WS_FRAME_EVENT, handleFrame)
    source.close()
    source = null
  }

  function teardown(): void {
    closed = true
    if (reconnectTimer !== null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    detachSource()
  }

  function scheduleReconnect(): void {
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (!closed) connect()
    }, computeReconnectDelay(attempt))
  }

  function connect(): void {
    if (closed) return
    detachSource()
    source = new EventSource(streamUrl(), { withCredentials: true })
    source.onopen = () => {
      // Do NOT reset the attempt budget here: a server that accepts the stream
      // and immediately closes it would otherwise let the client retry forever
      // at the base delay. The budget is only reset in ``onerror`` once a
      // connection has stayed open long enough to count as stable.
      openedAt = Date.now()
      if (lastEventId) {
        log.debug('SSE fallback (re)connected', sanitizeForLog({ lastEventId }))
      }
      callbacks.onOpen?.()
    }
    source.addEventListener(WS_FRAME_EVENT, handleFrame)
    source.onerror = () => {
      if (closed) return
      const wasStableOpen =
        openedAt > 0 && Date.now() - openedAt >= SSE_RECONNECT_MAX_DELAY
      openedAt = 0
      if (wasStableOpen) {
        attempt = 0
        reportedDisconnect = false
      }
      attempt += 1
      if (attempt > SSE_MAX_RECONNECT_ATTEMPTS) {
        log.error('SSE fallback exhausted its reconnect budget; closing')
        teardown()
        callbacks.onExhausted?.()
        return
      }
      // Close immediately so we own the retry cadence; the native flat-rate
      // retry would otherwise reconnect on its own without backoff.
      detachSource()
      if (!reportedDisconnect) {
        reportedDisconnect = true
        callbacks.onError(new Error('SSE transport error'))
      }
      scheduleReconnect()
    }
  }

  connect()
  return { close: teardown }
}

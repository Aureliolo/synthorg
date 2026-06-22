/**
 * SSE fallback transport.
 *
 * When the WebSocket is detected as proxy-blocked (two consecutive
 * 1006 closes with no `auth_ok`), the dashboard switches to a
 * read-only SSE feed against `/api/v1/events/stream` so the
 * tasks / approvals / agents / budget surfaces still update in
 * real time. Write-path features (chat, settings actions) surface
 * a "connection limited" banner via the notifications store and
 * fall back to REST polling on their own pages.
 *
 * The server emits AG-UI projected events (see
 * `src/synthorg/communication/event_stream/types.py`); the mapping
 * table below translates the small subset of AG-UI types we can
 * map cleanly to the dashboard's internal `WsEvent` shape. Types
 * without a mapping are dropped with a debug log -- they are
 * either streaming sub-frames (`text_message_*`, `tool_call_*`)
 * or HITL-only signals that the read-only fallback intentionally
 * does not surface.
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import {
  SSE_MAX_RECONNECT_ATTEMPTS,
  SSE_RECONNECT_BASE_DELAY,
  SSE_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'
import type { WsChannel, WsEvent } from '@/api/types/websocket'

const log = createLogger('sse-client')

const SSE_STREAM_PATH = '/api/v1/events/stream'

/**
 * AG-UI event type → internal {event_type, channel} mapping.
 *
 * ``event_type`` is typed as ``WsEvent['event_type']`` (the
 * ``WsEventType`` union) so a typo in a mapped value fails to compile
 * instead of slipping into the dispatch loop as an invalid wire-event
 * string. Pair with the unconditional ``WsChannel`` constraint on
 * ``channel`` and the bracket-access path returns a typed
 * ``AgUiMappedEvent`` -- the terminal envelope no longer needs an
 * ``as WsEvent`` cast.
 */
type AgUiMappedEvent = Readonly<{
  event_type: WsEvent['event_type']
  channel: WsChannel
}>

const AGUI_EVENT_MAP: Readonly<Record<string, AgUiMappedEvent>> = {
  run_started: { event_type: 'task.status_changed', channel: 'tasks' },
  run_finished: { event_type: 'task.status_changed', channel: 'tasks' },
  run_error: { event_type: 'task.status_changed', channel: 'tasks' },
  step_started: { event_type: 'task.updated', channel: 'tasks' },
  step_finished: { event_type: 'task.updated', channel: 'tasks' },
  step_failed: { event_type: 'task.updated', channel: 'tasks' },
  approval_interrupt: { event_type: 'approval.submitted', channel: 'approvals' },
  approval_resumed: { event_type: 'approval.approved', channel: 'approvals' },
}

interface SseRawEvent {
  // ``id`` is optional because ``mapAgUiToWsEvent`` never reads it;
  // making it required would discard otherwise-valid frames whose
  // server happens to omit ``id`` (or moves it into a different
  // envelope field on a future schema revision).
  id?: string
  type: string
  timestamp: string
  payload?: unknown
}

interface SseClientCallbacks {
  onEvent: (event: WsEvent) => void
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

/** Parse one SSE frame, surface its event id, and dispatch the mapped event. */
function processSseFrame(
  event: MessageEvent,
  onEvent: (event: WsEvent) => void,
  onLastEventId: (id: string) => void,
): void {
  if (event.lastEventId) {
    // Clamp the server-supplied id before we store / log it: it is
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
  const sseEvent = asSseRaw(parsed)
  if (sseEvent === null) {
    log.warn('SSE frame missing required fields, discarding')
    return
  }
  const mapped = mapAgUiToWsEvent(sseEvent)
  if (mapped === null) return
  onEvent(mapped)
}

/**
 * Open an SSE connection to the events stream and dispatch every
 * mappable AG-UI event as an internal `WsEvent`.
 *
 * Reconnection is driven at the application level (close + re-`new
 * EventSource`) with exponential backoff (`SSE_RECONNECT_BASE_DELAY`
 * doubling to `SSE_RECONNECT_MAX_DELAY`), mirroring the WS transport's
 * backoff policy: the browser's native `EventSource` retry is a flat
 * cadence with no backoff, so a prolonged outage would otherwise hammer
 * the backend. Returns a handle whose `close()` cancels any pending
 * reconnect timer and tears down the EventSource.
 *
 * NOTE on `Last-Event-ID` replay: because we drive reconnect ourselves
 * (a fresh `EventSource` each cycle) rather than relying on the browser's
 * native retry, the `Last-Event-ID` request header is NOT re-sent on
 * reconnect. This is acceptable: the backend events hub is a live queue
 * with no backing store and never replays from a cursor, so events
 * published during an outage are lost on both the native and the
 * app-level reconnect paths. `lastEventId` is retained for debug logging
 * only. If server-side replay is ever added, the cursor would need to be
 * threaded into the reconnect request here (e.g. a query parameter).
 */
export function openSseFallback(callbacks: SseClientCallbacks): SseClient {
  const url = SSE_STREAM_PATH
  // The server names every SSE frame with its AG-UI event type (the
  // ``event:`` field), so the unnamed ``onmessage`` handler would never
  // fire. Register the shared handler for each mappable type instead.
  const mappedTypes = Object.keys(AGUI_EVENT_MAP)

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

  const handleFrame = (event: MessageEvent): void => {
    processSseFrame(event, callbacks.onEvent, (id) => {
      lastEventId = id
    })
  }

  // Null handlers before closing so closure captures release promptly; some
  // engines do not free EventSource handlers on .close() alone.
  function detachSource(): void {
    if (!source) return
    source.onopen = null
    source.onerror = null
    for (const aguiType of mappedTypes) {
      source.removeEventListener(aguiType, handleFrame)
    }
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
    const delay = Math.min(
      SSE_RECONNECT_BASE_DELAY * 2 ** (attempt - 1),
      SSE_RECONNECT_MAX_DELAY,
    )
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (!closed) connect()
    }, delay)
  }

  function connect(): void {
    if (closed) return
    detachSource()
    source = new EventSource(url, { withCredentials: true })
    source.onopen = () => {
      attempt = 0
      reportedDisconnect = false
      if (lastEventId) {
        log.debug('SSE fallback (re)connected', sanitizeForLog({ lastEventId }))
      }
      callbacks.onOpen?.()
    }
    for (const aguiType of mappedTypes) {
      source.addEventListener(aguiType, handleFrame)
    }
    source.onerror = () => {
      if (closed) return
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

function asSseRaw(value: unknown): SseRawEvent | null {
  if (value === null || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  // Every WS-supplied string is clamped (C0 controls / bidi
  // overrides stripped, length capped at MAX_WS_STRING_LEN) before
  // it leaves the parser. ``sanitizeWsString`` returns ``undefined``
  // for non-strings, whitespace-only strings, and strings whose
  // post-sanitisation content is empty, so a single ``undefined``
  // check below rejects every malformed shape.
  const type = sanitizeWsString(record['type'])
  const timestamp = sanitizeWsString(record['timestamp'])
  // Only ``type`` and ``timestamp`` are required for the downstream
  // ``mapAgUiToWsEvent`` projection. ``id`` is preserved when present
  // (so logs / debug surfaces keep the server-side identifier) but a
  // frame without one is still mappable.
  if (type === undefined || timestamp === undefined) {
    return null
  }
  const id = sanitizeWsString(record['id'])
  return {
    type,
    timestamp,
    payload: record['payload'],
    ...(id !== undefined && { id }),
  }
}

function mapAgUiToWsEvent(sse: SseRawEvent): WsEvent | null {
  // ``sse.type`` already passed through ``sanitizeWsString`` in
  // ``asSseRaw``; the ``AGUI_EVENT_MAP`` lookup below is the enum
  // allowlist. Anything outside the table -- including a value the
  // server adds before the dashboard learns about it -- is dropped
  // with a debug log instead of being forwarded as a raw, un-typed
  // event_type. This is the SSE-side equivalent of the WS store's
  // ``sanitizeWsEnum`` "fall back to a known value" policy: we drop
  // rather than fall back because the read-only fallback should
  // never invent task / approval status transitions the server did
  // not actually emit.
  // ``Object.hasOwn`` (not bracket-then-undefined-check) is required
  // because bracket access against an untrusted key like ``constructor``
  // / ``__proto__`` / ``toString`` would resolve to the inherited
  // prototype property instead of ``undefined`` -- those values are
  // truthy and would slip past the ``=== undefined`` guard, producing
  // a frame with no event_type / channel.
  if (!Object.hasOwn(AGUI_EVENT_MAP, sse.type)) {
    log.debug('Unmapped AG-UI event type discarded', sanitizeForLog({ type: sse.type }))
    return null
  }
  // ``Object.hasOwn`` narrows runtime existence but not the TS
  // optional-property type; the explicit re-check keeps strict
  // ``noUncheckedIndexedAccess`` callers honest without changing
  // observable behaviour.
  const mapping = AGUI_EVENT_MAP[sse.type]
  if (mapping === undefined) return null
  // ``typeof null === 'object'`` is excluded above; the extra
  // ``!Array.isArray`` guard rejects array payloads too so the
  // outbound ``WsEvent.payload`` envelope stays a plain
  // ``Record<string, unknown>`` and downstream channel handlers
  // can rely on the shape.
  const payload = sse.payload !== undefined
    && sse.payload !== null
    && typeof sse.payload === 'object'
    && !Array.isArray(sse.payload)
    ? (sse.payload as Record<string, unknown>)
    : {}
  return {
    event_type: mapping.event_type,
    channel: mapping.channel,
    timestamp: sse.timestamp,
    payload,
  }
}

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
import { SSE_MAX_RECONNECT_ATTEMPTS } from '@/utils/constants'
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

/**
 * Open an SSE connection to the events stream and dispatch every
 * mappable AG-UI event as an internal `WsEvent`.
 *
 * Returns a handle whose `close()` tears down the EventSource. The
 * caller is responsible for installing reconnect / retry semantics
 * if the SSE stream itself drops; the dashboard's WS transport
 * orchestrator owns that policy.
 */
export function openSseFallback(callbacks: SseClientCallbacks): SseClient {
  const url = SSE_STREAM_PATH
  const source = new EventSource(url, { withCredentials: true })
  // Dedupe transient reconnect failures. EventSource's built-in
  // reconnect loop fires ``onerror`` on every transient blip while
  // it shifts back to ``CONNECTING`` and re-attempts the handshake;
  // the spec then fires ``onopen`` again on recovery. Reporting each
  // intermediate ``onerror`` to the caller would flood the operator
  // with toasts for a single network hiccup. The flag flips to
  // ``true`` on the first failure of a disconnect cycle and resets
  // in ``onopen`` so a genuine fresh failure (after a successful
  // re-open) still surfaces.
  let reportedDisconnect = false
  // Counts transport errors so a prolonged outage (EventSource retries
  // indefinitely on its own) gives up at SSE_MAX_RECONNECT_ATTEMPTS instead
  // of flooding the backend with reconnect traffic. Reset on a clean re-open.
  let errorCount = 0
  // Last server-sent event id. The browser auto-sends it as `Last-Event-ID`
  // on reconnect once the server emits `id:` lines, so missed events can be
  // replayed; we also surface it for debugging.
  let lastEventId = ''

  // Null handlers before closing so closure captures release promptly; some
  // engines do not free EventSource handlers on .close() alone.
  function teardown(): void {
    source.onopen = null
    source.onmessage = null
    source.onerror = null
    source.close()
  }

  source.onopen = () => {
    reportedDisconnect = false
    errorCount = 0
    if (lastEventId) {
      log.debug('SSE fallback (re)connected', sanitizeForLog({ lastEventId }))
    }
    callbacks.onOpen?.()
  }

  source.onmessage = (event: MessageEvent) => {
    if (event.lastEventId) {
      // Clamp the server-supplied id before we store / log it: it is
      // attacker-influenced and otherwise uncapped (control chars, bidi
      // overrides, unbounded length). The browser still sends the raw
      // value as `Last-Event-ID`; this only guards our own debug surface.
      const sanitizedId = sanitizeWsString(event.lastEventId)
      if (sanitizedId !== undefined) lastEventId = sanitizedId
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
    callbacks.onEvent(mapped)
  }

  source.onerror = () => {
    errorCount += 1
    if (errorCount >= SSE_MAX_RECONNECT_ATTEMPTS) {
      log.error('SSE fallback exhausted its reconnect budget; closing')
      teardown()
      callbacks.onExhausted?.()
      return
    }
    if (reportedDisconnect) return
    reportedDisconnect = true
    callbacks.onError(new Error('SSE transport error'))
  }

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
    id,
    type,
    timestamp,
    payload: record['payload'],
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

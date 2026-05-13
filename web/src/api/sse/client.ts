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
import type { WsChannel, WsEvent } from '@/api/types/websocket'

const log = createLogger('sse-client')

const SSE_STREAM_PATH = '/api/v1/events/stream'

/** AG-UI event type → internal {event_type, channel} mapping. */
const AGUI_EVENT_MAP: Readonly<Record<string, { event_type: string; channel: WsChannel }>> = {
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
  id: string
  type: string
  timestamp: string
  payload?: unknown
}

interface SseClientCallbacks {
  onEvent: (event: WsEvent) => void
  onError: (error: Error) => void
  onOpen?: () => void
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

  source.onopen = () => {
    callbacks.onOpen?.()
  }

  source.onmessage = (event: MessageEvent) => {
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
    const transportError = new Error('SSE transport error')
    callbacks.onError(transportError)
  }

  return {
    close() {
      source.close()
    },
  }
}

function asSseRaw(value: unknown): SseRawEvent | null {
  if (value === null || typeof value !== 'object') return null
  const record = value as Record<string, unknown>
  const id = record['id']
  const type = record['type']
  const timestamp = record['timestamp']
  if (typeof id !== 'string' || typeof type !== 'string' || typeof timestamp !== 'string') {
    return null
  }
  return {
    id,
    type,
    timestamp,
    payload: record['payload'],
  }
}

function mapAgUiToWsEvent(sse: SseRawEvent): WsEvent | null {
  const mapping = AGUI_EVENT_MAP[sse.type]
  if (mapping === undefined) {
    log.debug('Unmapped AG-UI event type discarded', sanitizeForLog(sse.type))
    return null
  }
  const payload = sse.payload !== undefined && sse.payload !== null && typeof sse.payload === 'object'
    ? (sse.payload as Record<string, unknown>)
    : {}
  return {
    event_type: mapping.event_type,
    channel: mapping.channel,
    timestamp: sse.timestamp,
    payload,
  } as WsEvent
}

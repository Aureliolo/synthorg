/**
 * Per-task AG-UI progress stream client.
 *
 * Opens an `EventSource` against `/api/v1/events/stream?session_id=<taskId>`
 * and forwards each parsed, sanitised AG-UI progress event to the caller. The
 * stream is owner/CEO-gated server-side (the session id is the task id), so a
 * caller subscribes only to a task it filed. Short-lived: a run terminates
 * with `run_finished` / `run_error`, at which point the caller closes.
 *
 * Distinct from `openSseFallback` (the session-less dashboard `WsEvent`
 * fallback): this stream is per-task and carries AG-UI `StreamEvent` frames
 * whose SSE `event:` name is the event type.
 */

import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import {
  SSE_MAX_RECONNECT_ATTEMPTS,
  SSE_RECONNECT_BASE_DELAY,
  SSE_RECONNECT_MAX_DELAY,
} from '@/utils/ws-constants'
import {
  AGUI_PROGRESS_EVENTS,
  parseAguiEvent,
  type AguiStreamEvent,
} from './agui-types'

const log = createLogger('task-progress-sse')

const STREAM_PATH = '/api/v1/events/stream'

export interface TaskProgressCallbacks {
  /** Invoked with each parsed, sanitised AG-UI progress event. */
  onEvent: (event: AguiStreamEvent) => void
  onError?: (error: Error) => void
  onOpen?: () => void
  /** Invoked once the reconnect budget is exhausted; the client is closed. */
  onExhausted?: () => void
}

export interface TaskProgressStream {
  close: () => void
}

function streamUrl(taskId: string, lastEventId: string): string {
  const params = new URLSearchParams({ session_id: taskId })
  if (lastEventId) params.set('last_event_id', lastEventId)
  return `${STREAM_PATH}?${params.toString()}`
}

function computeReconnectDelay(attempt: number): number {
  return Math.min(
    SSE_RECONNECT_BASE_DELAY * 2 ** (attempt - 1),
    SSE_RECONNECT_MAX_DELAY,
  )
}

/** Parse one SSE frame's data into a sanitised AG-UI event, or null. */
function parseProgressFrame(data: string): AguiStreamEvent | null {
  let parsed: unknown
  try {
    parsed = JSON.parse(data)
  } catch (err) {
    log.warn('Failed to parse task progress frame', sanitizeForLog(err))
    return null
  }
  return parseAguiEvent(parsed)
}

/**
 * Subscribe to a task's AG-UI progress stream. Returns a handle whose
 * `close()` tears down the `EventSource` and cancels any pending reconnect.
 */
export function openTaskProgressStream(
  taskId: string,
  callbacks: TaskProgressCallbacks,
): TaskProgressStream {
  let source: EventSource | null = null
  let reconnectTimer: ReturnType<typeof setTimeout> | null = null
  let closed = false
  // Reconnect attempts since the last STABLE open; drives both the backoff
  // delay and the SSE_MAX_RECONNECT_ATTEMPTS budget. Reset in ``onerror`` (not
  // ``onopen``) once a connection has stayed open long enough to count stable.
  let attempt = 0
  // Wall-clock of the last open; 0 while disconnected. Distinguishes a stable
  // connection from a short-lived flap so a server that accepts then instantly
  // drops the stream cannot let the client retry forever at the base delay.
  let openedAt = 0
  let lastEventId = ''

  const handleFrame = (event: MessageEvent): void => {
    if (event.lastEventId) lastEventId = event.lastEventId
    if (typeof event.data !== 'string') return
    const aguiEvent = parseProgressFrame(event.data)
    if (aguiEvent !== null) callbacks.onEvent(aguiEvent)
  }

  function detachSource(): void {
    if (!source) return
    source.onopen = null
    source.onerror = null
    for (const name of AGUI_PROGRESS_EVENTS) {
      source.removeEventListener(name, handleFrame)
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
    reconnectTimer = setTimeout(() => {
      reconnectTimer = null
      if (!closed) connect()
    }, computeReconnectDelay(attempt))
  }

  function connect(): void {
    if (closed) return
    detachSource()
    source = new EventSource(streamUrl(taskId, lastEventId), {
      withCredentials: true,
    })
    source.onopen = () => {
      // Do NOT reset the attempt budget here: a server that accepts the stream
      // and immediately closes it would otherwise let the client retry forever
      // at the base delay. The budget resets in ``onerror`` only once the
      // connection has stayed open long enough to count as stable.
      openedAt = Date.now()
      callbacks.onOpen?.()
    }
    for (const name of AGUI_PROGRESS_EVENTS) {
      source.addEventListener(name, handleFrame)
    }
    source.onerror = () => {
      if (closed) return
      const wasStableOpen =
        openedAt > 0 && Date.now() - openedAt >= SSE_RECONNECT_MAX_DELAY
      openedAt = 0
      if (wasStableOpen) attempt = 0
      attempt += 1
      if (attempt > SSE_MAX_RECONNECT_ATTEMPTS) {
        log.warn('Task progress stream exhausted its reconnect budget; closing')
        teardown()
        callbacks.onExhausted?.()
        return
      }
      detachSource()
      callbacks.onError?.(new Error('Task progress stream error'))
      scheduleReconnect()
    }
  }

  connect()
  return { close: teardown }
}

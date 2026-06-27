import { openSseFallback } from '@/api/sse/client'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { asObjectRecord } from '@/utils/parse'
import { eventVersion, isWsEvent } from './dispatch'
import { isSupportedWireVersion } from './protocol-guard'
import { dispatchEvent } from './subscriptions'
import type { WsSet } from './types'

const log = createLogger('ws')

/**
 * SSE fallback transport bookkeeping. When the WS handshake fails
 * twice in a row with a 1006 close (proxy-blocked WS upgrade is the
 * canonical failure mode), the store switches to a read-only SSE
 * feed against ``/api/v1/events/dashboard``. Each raw SSE payload is
 * validated as a ``WsEvent`` (via ``isWsEvent``) and version-gated,
 * then routed through the same ``dispatchEvent`` path as the WebSocket
 * transport so the dashboard's tasks / agents / approvals / budget
 * stores keep updating; write-path features rely on the
 * ``connection.limited`` toast to direct the user.
 */
let sseClient: { close: () => void } | null = null
let proxyBlockSuspicion = 0
const PROXY_BLOCK_THRESHOLD = 2

// Reconnect deduplication: the backend replays the recent per-channel backlog
// on reconnect (gap recovery), so the client tracks recently-dispatched
// ``event_id``s and drops a replayed duplicate. Bounded so a long-lived
// session cannot grow it without limit; insertion-ordered eviction is enough
// since ids are only re-seen within the small replay window.
const SEEN_EVENT_ID_LIMIT = 512
const seenEventIds = new Set<string>()

function rememberEventId(id: string): void {
  seenEventIds.add(id)
  if (seenEventIds.size > SEEN_EVENT_ID_LIMIT) {
    const oldest = seenEventIds.values().next().value
    if (oldest !== undefined) seenEventIds.delete(oldest)
  }
}

export function isSseFallbackActive(): boolean {
  return sseClient !== null
}

export function closeSseFallback(set?: WsSet): void {
  if (sseClient) {
    sseClient.close()
    sseClient = null
  }
  seenEventIds.clear()
  set?.({ sseFallbackActive: false, sseFallbackExhausted: false })
}

export function resetProxyBlockSuspicion(): void {
  proxyBlockSuspicion = 0
}

/**
 * Record a 1006 close that fired before ``auth_ok``. Returns true
 * when the suspicion count reaches the activation threshold AND no
 * SSE fallback is already running, signalling the caller to wire up
 * the read-only transport.
 */
export function recordAbnormalCloseDuringHandshake(): boolean {
  proxyBlockSuspicion += 1
  return proxyBlockSuspicion >= PROXY_BLOCK_THRESHOLD && sseClient === null
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
      description:
        'Real-time WebSocket is blocked. Falling back to SSE; some interactive features (chat, settings actions) may be unavailable until you reload after fixing your proxy.',
    })
  } catch (err) {
    // Capture the error type so a ChunkLoadError (deploy-time hash
    // change) is distinguishable from a store init failure.
    log.warn('Could not surface connection-limited toast', {
      errorType: err instanceof Error ? err.name : typeof err,
      error: sanitizeForLog(err instanceof Error ? err.message : String(err)),
    })
    log.warn(
      'SSE fallback active; chat and settings features unavailable until reload',
    )
  }
}

export function activateSseFallback(set: WsSet): void {
  if (sseClient !== null) return
  log.warn('WS handshake repeatedly failed with 1006; activating SSE fallback')
  sseClient = openSseFallback({
    onOpen: () => {
      log.debug('SSE fallback connected')
      // A clean (re)open means the fallback is live again; clear any prior
      // exhausted flag so the banner returns to the "degraded" state.
      set({ sseFallbackExhausted: false })
    },
    onEvent: (raw) => {
      // The dashboard SSE feed carries the same WsEvent payloads as the
      // socket, so validate + version-gate them through the identical path
      // before dispatch rather than trusting the wire shape. Return false on
      // every reject path so the transport does not advance the replay cursor
      // past a frame we dropped.
      const msg = asObjectRecord(raw)
      if (msg === null) {
        log.warn('SSE event was not a plain object, discarding', {
          rawType: typeof raw,
        })
        return false
      }
      if (!isWsEvent(msg)) {
        log.warn('SSE event failed WsEvent schema validation, discarding', {
          eventType: sanitizeForLog(msg['event_type']),
          channel: sanitizeForLog(msg['channel']),
        })
        return false
      }
      if (!isSupportedWireVersion(eventVersion(msg), msg, set)) return false
      // Drop a replayed duplicate (reconnect backlog) so each event dispatches
      // once; treat it as handled so the replay cursor still advances past it.
      const eventId = typeof msg['event_id'] === 'string' ? msg['event_id'] : null
      if (eventId !== null) {
        if (seenEventIds.has(eventId)) return true
        rememberEventId(eventId)
      }
      dispatchEvent(msg)
      return true
    },
    onError: (err) => {
      log.warn('SSE fallback transport error', sanitizeForLog(err.message))
    },
    onExhausted: () => {
      log.error('SSE fallback exhausted; no live transport remains')
      // Clear the client ref and active flag so the ``activateSseFallback``
      // guard (``sseClient !== null``) does not strand re-activation: an
      // exhausted client delivers nothing, so a later handshake-failure path
      // must be able to open a fresh fallback.
      sseClient = null
      set({ sseFallbackActive: false, sseFallbackExhausted: true })
    },
  })
  set({ sseFallbackActive: true, sseFallbackExhausted: false })
  void notifyConnectionLimited()
}

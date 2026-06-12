import { openSseFallback } from '@/api/sse/client'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import { dispatchEvent } from './subscriptions'
import type { WsSet } from './types'

const log = createLogger('ws')

/**
 * SSE fallback transport bookkeeping. When the WS handshake fails
 * twice in a row with a 1006 close (proxy-blocked WS upgrade is the
 * canonical failure mode), the store switches to a read-only SSE
 * feed against ``/api/v1/events/stream``. The fallback dispatches
 * AG-UI projected events through the same ``dispatchEvent`` handler
 * chain so the dashboard's tasks / agents / approvals / budget
 * stores keep updating; write-path features rely on the
 * ``connection.limited`` toast to direct the user.
 */
let sseClient: { close: () => void } | null = null
let proxyBlockSuspicion = 0
const PROXY_BLOCK_THRESHOLD = 2

export function isSseFallbackActive(): boolean {
  return sseClient !== null
}

export function closeSseFallback(set?: WsSet): void {
  if (sseClient) {
    sseClient.close()
    sseClient = null
  }
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
    log.warn('Could not surface connection-limited toast', sanitizeForLog(err))
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
    onEvent: (wsEvent) => {
      dispatchEvent(wsEvent)
    },
    onError: (err) => {
      log.warn('SSE fallback transport error', sanitizeForLog(err.message))
    },
    onExhausted: () => {
      log.error('SSE fallback exhausted; no live transport remains')
      set({ sseFallbackExhausted: true })
    },
  })
  set({ sseFallbackActive: true, sseFallbackExhausted: false })
  void notifyConnectionLimited()
}

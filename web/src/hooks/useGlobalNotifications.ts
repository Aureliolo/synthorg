import { useEffect, useMemo, useRef } from 'react'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { useAgentsStore } from '@/stores/agents'
import { useNotificationsStore } from '@/stores/notifications'
import { useWebSocketStore } from '@/stores/websocket'
import { createLogger } from '@/lib/logger'
import { sanitizeForLog } from '@/utils/logging'
import type { WsChannel } from '@/api/types/websocket'

const log = createLogger('useGlobalNotifications')

/**
 * Subscribe globally to WebSocket channels that drive app-wide notifications.
 *
 * Mounted once at the {@link AppLayout} level so notifications render regardless
 * of which page the user is currently viewing. All WS events are routed through
 * the unified notification store which handles fan-out to toast, drawer, and
 * browser notifications based on category routing config.
 *
 * Channel-specific stores (e.g. agents) are still updated directly for their
 * domain-specific state tracking (runtime statuses, etc.).
 *
 * Connection failures are surfaced via the notification pipeline: initial loss
 * goes to toast-only (`connection.lost`), exhausted reconnect goes to drawer +
 * toast + browser (`connection.exhausted`).
 *
 * This hook is intentionally minimal -- it only covers *global* notifications.
 * Page-scoped WebSocket handling remains in the per-page data hooks.
 */
// NOTE: 'providers' and 'connection' channels are not included yet -- the
// backend does not emit those WS events. Add them here once backend support
// lands so the notification pipeline can route provider.* and connection.*
// categories end-to-end.
const GLOBAL_CHANNELS = [
  'agents',
  'approvals',
  'budget',
  'system',
  'tasks',
] as const satisfies readonly WsChannel[]

export function useGlobalNotifications(): void {
  const bindings: ChannelBinding[] = useMemo(
    () =>
      GLOBAL_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          // Route all events through the unified notification pipeline.
          // Store methods log + swallow internally; callers MUST NOT
          // wrap them in try/catch (the store owns the error UX).
          useNotificationsStore.getState().handleWsEvent(event)

          // Agents channel: also update agent-specific store state
          // (runtime statuses, personality tracking, etc.)
          if (channel === 'agents') {
            useAgentsStore.getState().updateFromWsEvent(event)
          }
        },
      })),
    [],
  )

  const { setupError, reconnectExhausted, connected } = useWebSocket({ bindings })

  // Surface setup errors via the notification pipeline.
  const lastSetupErrorRef = useRef<string | null>(null)
  useEffect(() => {
    if (setupError && setupError !== lastSetupErrorRef.current) {
      lastSetupErrorRef.current = setupError
      log.warn('Global notifications WebSocket setup failed', {
        setupError: sanitizeForLog(setupError),
      })
      useNotificationsStore.getState().enqueue({
        category: 'connection.lost',
        title: 'Live notifications unavailable',
        description: 'You may miss real-time updates. Try refreshing the page.',
      })
    }
  }, [setupError])

  // Surface reconnect exhaustion as a more severe notification, with an
  // inline Retry action so the user can recover without a full page
  // refresh. The Retry calls back into the WebSocket store to reset the
  // reconnect budget and start a fresh attempt.
  const reconnectExhaustedRef = useRef(false)
  useEffect(() => {
    if (reconnectExhausted && !reconnectExhaustedRef.current) {
      reconnectExhaustedRef.current = true
      log.error('Global notifications reconnect exhausted')
      useNotificationsStore.getState().enqueue({
        category: 'connection.exhausted',
        title: 'Live notifications disconnected',
        description:
          'Reconnect attempts exhausted. Click Retry to try again, or refresh the page.',
        toastAction: {
          label: 'Retry',
          onClick: () => {
            void useWebSocketStore.getState().retry()
          },
        },
      })
    }
  }, [reconnectExhausted])

  // Reset the one-shot refs when the WS successfully reconnects so a flapping
  // connection (transient network loss, backend restart) can emit a fresh
  // notification on the next failure instead of staying silent forever.
  useEffect(() => {
    if (connected) {
      reconnectExhaustedRef.current = false
      lastSetupErrorRef.current = null
    }
  }, [connected])
}

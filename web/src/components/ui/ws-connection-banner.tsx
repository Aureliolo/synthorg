import { ErrorBanner } from '@/components/ui/error-banner'
import { useWebSocketStore } from '@/stores/websocket'

export interface WsConnectionBannerProps {
  /**
   * Override the banner title. Defaults to the dashboard's standard
   * "Real-time updates disconnected" copy used elsewhere.
   */
  title?: string
  /**
   * Override the banner description with page-specific freshness
   * guidance (e.g. "Connection state may be stale until reconnect.").
   * Defaults to a generic message that matches the other dashboards.
   */
  description?: string
}

/**
 * Page-level WebSocket-offline banner. Reads the connection state
 * directly from the websocket store so callers do not need to wire a
 * channel subscription just to surface the offline signal. Renders
 * nothing while the socket is connected; emits an offline
 * ``<ErrorBanner>`` when it is not.
 */
export function WsConnectionBanner({
  title = 'Real-time updates disconnected',
  description = 'Data may be stale until the connection recovers.',
}: WsConnectionBannerProps = {}) {
  const connected = useWebSocketStore((s) => s.connected)
  if (connected) return null
  return <ErrorBanner variant="offline" title={title} description={description} />
}

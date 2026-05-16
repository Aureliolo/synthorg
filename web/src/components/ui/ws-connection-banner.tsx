import { useEffect, useRef, useState } from 'react'
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

const DEFAULT_TITLE = 'Real-time updates disconnected'
const DEFAULT_DESCRIPTION = 'Data may be stale until the connection recovers.'

// Grace window during which the initial-handshake transition is
// allowed to stay silent. A session that starts offline and never
// connects will still surface the banner once this timer elapses --
// previously the ``everConnectedRef`` suppression kept it hidden
// indefinitely, which masked the exact failure mode this banner
// exists to communicate.
const INITIAL_HANDSHAKE_GRACE_MS = 5000

/**
 * Page-level WebSocket-offline banner. Reads the connection state
 * directly from the websocket store so callers do not need to wire a
 * channel subscription just to surface the offline signal. Renders
 * nothing while the socket is connected. The banner is suppressed
 * only during the brief initial-handshake window (the WS store boots
 * at ``connected: false``); after that window, or after the first
 * successful connect, an offline state always surfaces.
 */
export function WsConnectionBanner({
  title = DEFAULT_TITLE,
  description = DEFAULT_DESCRIPTION,
}: WsConnectionBannerProps = {}) {
  const connected = useWebSocketStore((s) => s.connected)
  const everConnectedRef = useRef(false)
  const [initialGraceElapsed, setInitialGraceElapsed] = useState(false)

  useEffect(() => {
    const id = window.setTimeout(
      () => setInitialGraceElapsed(true),
      INITIAL_HANDSHAKE_GRACE_MS,
    )
    return () => window.clearTimeout(id)
  }, [])

  useEffect(() => {
    if (connected) everConnectedRef.current = true
  }, [connected])

  if (connected) return null
  if (!everConnectedRef.current && !initialGraceElapsed) return null
  return <ErrorBanner variant="offline" title={title} description={description} />
}

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

type BannerKind = 'mismatch' | 'sse-degraded' | 'offline' | null

/**
 * Decide which (if any) connection banner to show from the websocket
 * store flags. Kept as a pure helper so the component body stays under
 * the complexity cap and the precedence (mismatch > connected >
 * SSE-degraded > offline) is expressed in one readable ladder.
 */
function _resolveBannerKind(flags: {
  connected: boolean
  protocolVersionMismatch: boolean
  sseFallbackActive: boolean
  sseFallbackExhausted: boolean
  showOffline: boolean
}): BannerKind {
  // The socket appears connected but events no longer decode: most
  // severe, surfaced even while `connected` is true so the operator can
  // reload.
  if (flags.protocolVersionMismatch) return 'mismatch'
  if (flags.connected) return null
  // Degraded but live: events are arriving over the read-only SSE fallback.
  if (flags.sseFallbackActive && !flags.sseFallbackExhausted) return 'sse-degraded'
  return flags.showOffline ? 'offline' : null
}

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
  const sseFallbackActive = useWebSocketStore((s) => s.sseFallbackActive)
  const sseFallbackExhausted = useWebSocketStore((s) => s.sseFallbackExhausted)
  const protocolVersionMismatch = useWebSocketStore((s) => s.protocolVersionMismatch)
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

  const showOffline = everConnectedRef.current || initialGraceElapsed
  const kind = _resolveBannerKind({
    connected,
    protocolVersionMismatch,
    sseFallbackActive,
    sseFallbackExhausted,
    showOffline,
  })

  if (kind === 'mismatch') {
    return (
      <ErrorBanner
        variant="section"
        severity="error"
        title="Update required"
        description="The server updated its real-time protocol. Reload the page to keep receiving live updates."
      />
    )
  }
  if (kind === 'sse-degraded') {
    return (
      <ErrorBanner
        variant="section"
        severity="warning"
        title="Real-time updates degraded"
        description="WebSocket is blocked; updates are arriving over a read-only fallback. Some interactive features may be unavailable until you reload."
      />
    )
  }
  if (kind === 'offline') {
    return <ErrorBanner variant="offline" title={title} description={description} />
  }
  return null
}

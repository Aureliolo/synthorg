/**
 * Bind a single ``(channel, handler)`` pair to the WebSocket store
 * for the lifetime of a React component, with automatic cleanup on
 * unmount or when an external ``AbortSignal`` fires.
 *
 * This hook closes the leak risk of calling
 * ``wsStore.onChannelEvent`` directly: a component that forgets the
 * paired ``offChannelEvent`` would leak the handler in the
 * module-scoped registration map for the lifetime of the process.
 * ``useEffect``'s cleanup is the canonical React pattern; the
 * optional ``signal`` argument lets ad-hoc callers (e.g. an upstream
 * fetch abort, a user-cancelled long-running mutation) tear the
 * binding down before the component itself unmounts.
 */
import { useEffect } from 'react'

import type { WsChannel, WsEventHandler } from '@/api/types/websocket'
import { useWebSocketStore } from '@/stores/websocket'

export function useChannelHandler(
  channel: WsChannel | '*',
  handler: WsEventHandler,
  signal?: AbortSignal,
): void {
  useEffect(() => {
    if (signal?.aborted) return undefined

    const wsStore = useWebSocketStore.getState()
    wsStore.onChannelEvent(channel, handler)

    let detached = false
    const detach = (): void => {
      if (detached) return
      detached = true
      wsStore.offChannelEvent(channel, handler)
    }

    signal?.addEventListener('abort', detach)
    return () => {
      signal?.removeEventListener('abort', detach)
      detach()
    }
  }, [channel, handler, signal])
}

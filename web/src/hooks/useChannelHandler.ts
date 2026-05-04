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
 *
 * Wrap ``handler`` in ``useCallback`` at the call site. The effect
 * deps include ``handler``, so a fresh function reference each
 * parent render would re-run the effect (off-then-on), churning the
 * store registration map. The ``detached`` guard makes the churn
 * idempotent but still adds work the caller can avoid by memoizing.
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
      // Order matters: drop the abort listener BEFORE invoking
      // detach(). If the listener fired during cleanup it would
      // hit the same detach() (idempotent via the ``detached``
      // guard), but removing the listener first keeps the cleanup
      // paths single-purpose and avoids a redundant scheduled
      // microtask in the abort propagation path.
      signal?.removeEventListener('abort', detach)
      detach()
    }
  }, [channel, handler, signal])
}

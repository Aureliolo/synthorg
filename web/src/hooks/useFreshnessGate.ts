import { useCallback, useRef } from 'react'
import { FRESHNESS_WINDOW_MS } from '@/utils/constants'

export interface FreshnessGate {
  /**
   * True when a WS-driven update arrived within `FRESHNESS_WINDOW_MS`. Pass as
   * `usePolling`'s `skipIfFresh` so a WS-active session does not also REST-poll
   * on cadence.
   */
  skipIfFresh: () => boolean
  /** Call from a WS event handler to mark store state freshly updated. */
  markFresh: () => void
}

/**
 * Shared freshness gate for hooks that both poll over REST and subscribe to a
 * WS channel. Tracks the last WS-update timestamp in a ref so the scheduled
 * poll can skip while real-time events are flowing.
 */
export function useFreshnessGate(): FreshnessGate {
  const lastWsUpdateAtRef = useRef<number>(0)
  const skipIfFresh = useCallback(
    () => Date.now() - lastWsUpdateAtRef.current < FRESHNESS_WINDOW_MS,
    [],
  )
  const markFresh = useCallback(() => {
    lastWsUpdateAtRef.current = Date.now()
  }, [])
  return { skipIfFresh, markFresh }
}

import { useCallback, useRef } from 'react'
import { FRESHNESS_WINDOW_MS, MAX_CONSECUTIVE_FRESH_SKIPS } from '@/utils/ws-constants'

export interface FreshnessGate {
  /**
   * True when a WS-driven update arrived within `FRESHNESS_WINDOW_MS` AND the
   * poll has not already been skipped `MAX_CONSECUTIVE_FRESH_SKIPS` times in a
   * row. Pass as `usePolling`'s `skipIfFresh` so a WS-active session does not
   * also REST-poll on cadence.
   */
  skipIfFresh: () => boolean
  /** Call from a WS event handler to mark store state freshly updated. */
  markFresh: () => void
}

/**
 * Shared freshness gate for hooks that both poll over REST and subscribe to a
 * WS channel. Tracks the last WS-update timestamp in a ref so the scheduled
 * poll can skip while real-time events are flowing.
 *
 * The skip is bounded, and that bound is load-bearing rather than defensive.
 * A WS frame can only ever ADD or UPDATE a row in a store; the REST refetch is
 * the only thing that reconciles, because it replaces the list with what the
 * server actually holds. So while frames arrive faster than the freshness
 * window, an unbounded gate lets a store keep rows that no longer exist, with
 * a full page reload as the only cure. That is how a sidebar badge came to
 * report three plan reviews against a page showing none.
 */
export function useFreshnessGate(): FreshnessGate {
  const lastWsUpdateAtRef = useRef<number>(0)
  const consecutiveSkipsRef = useRef<number>(0)
  const skipIfFresh = useCallback(() => {
    const fresh = Date.now() - lastWsUpdateAtRef.current < FRESHNESS_WINDOW_MS
    if (!fresh || consecutiveSkipsRef.current >= MAX_CONSECUTIVE_FRESH_SKIPS) {
      consecutiveSkipsRef.current = 0
      return false
    }
    consecutiveSkipsRef.current += 1
    return true
  }, [])
  const markFresh = useCallback(() => {
    lastWsUpdateAtRef.current = Date.now()
  }, [])
  return { skipIfFresh, markFresh }
}

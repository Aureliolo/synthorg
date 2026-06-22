import { useEffect, useRef, useState } from 'react'
import { useMessagesStore } from '@/stores/messages'
import { LIVE_EDGE_ACTIVE_MS } from '@/stores/org-chart-prefs'

/**
 * Track which org-chart hierarchy edges are currently "live" based
 * on recent message activity.
 *
 * Subscribes to the messages store via Zustand's subscribe API.
 * Whenever new messages arrive with a sender/recipient pair that
 * happens to correspond to a hierarchy edge, that edge is marked
 * active for `LIVE_EDGE_ACTIVE_MS` milliseconds.  A cleanup timer
 * expires stale entries on a 500ms interval and triggers a
 * re-render so the edges go dark again.
 *
 * Returns a Set of edge IDs that should render particles.  In
 * `always` mode the caller can ignore this and use all edges; in
 * `live` mode the caller intersects the rendered edges with this
 * set; in `off` mode the caller returns an empty set.
 */
/**
 * Callers MUST pass a memoised `edgeIdByAgentPair` (build it with `useMemo`
 * upstream). It is a dependency of the subscribe effect below, so a fresh
 * `Map` reference on every parent render would tear down and re-establish the
 * Zustand subscription each render, churning closures that retain the previous
 * map and the expiry ref until GC.
 */
export function useLiveEdgeActivity(
  edgeIdByAgentPair: ReadonlyMap<string, string>,
): ReadonlySet<string> {
  const [activeEdgeIds, setActiveEdgeIds] = useState<Set<string>>(() => new Set())
  const expiryMapRef = useRef<Map<string, number>>(new Map())

  // Subscribe to new messages and mark matching edges active.  Using
  // Zustand's subscribe (not a selector) means we avoid re-rendering
  // on every message for which no edge matches -- only matched
  // messages update state, and we rebuild the Set so React can pick
  // up the change via reference comparison.
  useEffect(() => {
    const unsubscribe = useMessagesStore.subscribe((state, prevState) => {
      if (state.messages === prevState.messages) return
      const prevIds = new Set(prevState.messages.map((m) => m.id))
      const newMessages = state.messages.filter((m) => !prevIds.has(m.id))
      if (newMessages.length === 0) return

      const now = Date.now()
      const expiry = expiryMapRef.current
      let changed = false
      for (const msg of newMessages) {
        if (!msg.sender || !msg.to) continue
        const key = `${msg.sender}::${msg.to}`
        const edgeId = edgeIdByAgentPair.get(key)
        if (!edgeId) continue
        expiry.set(edgeId, now + LIVE_EDGE_ACTIVE_MS)
        changed = true
      }
      if (changed) {
        setActiveEdgeIds(new Set(expiry.keys()))
      }
    })
    return () => unsubscribe()
  }, [edgeIdByAgentPair])

  // Cleanup expired entries every 500ms.  We snapshot into a local
  // array so we can mutate the ref without tripping React rules.
  useEffect(() => {
    const interval = window.setInterval(() => {
      const now = Date.now()
      const expiry = expiryMapRef.current
      let changed = false
      for (const [edgeId, expiresAt] of expiry) {
        if (expiresAt <= now) {
          expiry.delete(edgeId)
          changed = true
        }
      }
      if (changed) {
        setActiveEdgeIds(new Set(expiry.keys()))
      }
    }, 500)
    return () => window.clearInterval(interval)
  }, [])

  return activeEdgeIds
}

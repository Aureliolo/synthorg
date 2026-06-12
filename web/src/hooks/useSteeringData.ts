import { useCallback, useEffect, useMemo, useRef } from 'react'

import type { WsEvent } from '@/api/types/websocket'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { useSteeringStore } from '@/stores/steering'
import { sanitizeWsString } from '@/utils/ws-sanitize'

/** Steering events that mean the active-directive board changed for a project. */
const REFRESH_EVENTS = new Set<WsEvent['event_type']>([
  'steering.directive.issued',
  'steering.tasks.superseded',
])

export interface UseSteeringDataReturn {
  wsConnected: boolean
  wsSetupError: string | null
}

/**
 * Fetch the active steering directives for a project and keep them fresh.
 *
 * Re-fetches on the cockpit-channel ``steering.directive.issued`` /
 * ``steering.tasks.superseded`` events so a directive another operator
 * issues (or a supersession another operator confirms) appears without a
 * manual refresh. A blank ``projectId`` is a no-op (nothing to scope to).
 */
export function useSteeringData(projectId: string): UseSteeringDataReturn {
  const fetchDirectives = useSteeringStore((s) => s.fetchDirectives)

  // The WS handler is registered once on mount (useWebSocket binds its
  // bindings a single time). Read projectId from a ref kept current each
  // render so the registered handler always sees the live value rather than
  // the stale closure captured at first mount.
  const projectIdRef = useRef(projectId)
  useEffect(() => {
    projectIdRef.current = projectId
  })

  useEffect(() => {
    if (projectId.trim() === '') return
    void fetchDirectives(projectId)
  }, [projectId, fetchDirectives])

  const handleSteeringEvent = useCallback(
    (event: WsEvent) => {
      const pid = projectIdRef.current
      if (!REFRESH_EVENTS.has(event.event_type)) return
      if (pid.trim() === '') return
      const eventProject = sanitizeWsString(event.payload['project_id'])
      if (eventProject !== pid) return
      void fetchDirectives(pid)
    },
    [fetchDirectives],
  )

  const bindings: ChannelBinding[] = useMemo(
    () => [{ channel: 'cockpit', handler: handleSteeringEvent }],
    [handleSteeringEvent],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return { wsConnected, wsSetupError }
}

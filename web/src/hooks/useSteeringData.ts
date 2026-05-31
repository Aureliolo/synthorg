import { useCallback, useEffect, useMemo } from 'react'

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

  useEffect(() => {
    if (projectId.trim() === '') return
    void fetchDirectives(projectId)
  }, [projectId, fetchDirectives])

  const handleSteeringEvent = useCallback(
    (event: WsEvent) => {
      if (!REFRESH_EVENTS.has(event.event_type)) return
      if (projectId.trim() === '') return
      const eventProject = sanitizeWsString(event.payload.project_id)
      if (eventProject !== projectId) return
      void fetchDirectives(projectId)
    },
    [projectId, fetchDirectives],
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

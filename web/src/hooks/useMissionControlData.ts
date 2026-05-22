import { useCallback, useEffect, useMemo, useRef } from 'react'

import type { LiveActivitySnapshot } from '@/api/types'
import type { WsChannel } from '@/api/types/websocket'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { useMissionControlStore } from '@/stores/mission-control'

const SNAPSHOT_POLL_INTERVAL = 5_000
/**
 * Window after a WS event during which the scheduled snapshot poll skips
 * its fetch, so a burst of task/agent events does not trigger redundant
 * snapshot rebuilds while still guaranteeing eventual freshness.
 */
const SNAPSHOT_FRESHNESS_WINDOW_MS = 3_000
const COCKPIT_CHANNELS = [
  'cockpit',
  'tasks',
  'agents',
  'budget',
] as const satisfies readonly WsChannel[]

export interface UseMissionControlDataReturn {
  snapshot: LiveActivitySnapshot | null
  loading: boolean
  error: string | null
  isRefetching: boolean
  wsConnected: boolean
  wsSetupError: string | null
}

export function useMissionControlData(): UseMissionControlDataReturn {
  const snapshot = useMissionControlStore((s) => s.snapshot)
  const loading = useMissionControlStore((s) => s.snapshotLoading)
  const error = useMissionControlStore((s) => s.snapshotError)

  useEffect(() => {
    void useMissionControlStore.getState().fetchSnapshot()
  }, [])

  const lastWsUpdateAtRef = useRef<number>(0)
  // Coalesce burst-y WS-triggered refreshes: a flurry of cockpit/tasks/
  // agents events arriving within a few ms must collapse into a single
  // ``fetchSnapshot`` call so the dashboard never sees overlapping
  // requests racing stale responses over fresh ones.
  const snapshotFetchInFlightRef = useRef<boolean>(false)

  const refreshSnapshot = useCallback(async () => {
    if (snapshotFetchInFlightRef.current) return
    snapshotFetchInFlightRef.current = true
    try {
      await useMissionControlStore.getState().fetchSnapshot()
    } finally {
      snapshotFetchInFlightRef.current = false
    }
  }, [])

  const pollFn = useCallback(async () => {
    await refreshSnapshot()
  }, [refreshSnapshot])
  const skipIfFresh = useCallback(
    () => Date.now() - lastWsUpdateAtRef.current < SNAPSHOT_FRESHNESS_WINDOW_MS,
    [],
  )
  const polling = usePolling(pollFn, SNAPSHOT_POLL_INTERVAL, { skipIfFresh })

  useEffect(() => {
    polling.start()
    return () => polling.stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      COCKPIT_CHANNELS.map((channel) => ({
        channel,
        handler: () => {
          lastWsUpdateAtRef.current = Date.now()
          void refreshSnapshot()
        },
      })),
    [refreshSnapshot],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    snapshot,
    loading,
    error,
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
  }
}

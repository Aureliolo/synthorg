import { useCallback, useEffect, useMemo } from 'react'

import type { WsChannel } from '@/api/types/websocket'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { usePolling } from '@/hooks/usePolling'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { useScalingStore } from '@/stores/scaling'
import type {
  ScalingDecisionResponse,
  ScalingSignalResponse,
  ScalingStrategyResponse,
} from '@/api/endpoints/scaling'

const SCALING_POLL_INTERVAL = 30_000
const SCALING_CHANNELS = ['scaling'] as const satisfies readonly WsChannel[]

export interface UseScalingDataReturn {
  strategies: readonly ScalingStrategyResponse[]
  decisions: readonly ScalingDecisionResponse[]
  signals: readonly ScalingSignalResponse[]
  totalDecisions: number
  loading: boolean
  error: string | null
  evaluating: boolean
  isRefetching: boolean
  wsConnected: boolean
  wsSetupError: string | null
  evaluateNow: () => Promise<ScalingDecisionResponse[]>
}

export function useScalingData(): UseScalingDataReturn {
  // Granular selectors for re-render optimization.
  const strategies = useScalingStore((s) => s.strategies)
  const decisions = useScalingStore((s) => s.decisions)
  const signals = useScalingStore((s) => s.signals)
  const totalDecisions = useScalingStore((s) => s.totalDecisions)
  const loading = useScalingStore((s) => s.loading)
  const error = useScalingStore((s) => s.error)
  const evaluating = useScalingStore((s) => s.evaluating)
  const evaluateNow = useScalingStore((s) => s.evaluateNow)

  // Initial fetch.
  useEffect(() => {
    void useScalingStore.getState().fetchAll()
  }, [])

  // Polling for lightweight refresh, gated so a live WS push skips the next
  // redundant poll (matches the other WS-backed data hooks).
  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await useScalingStore.getState().fetchDecisions()
    await useScalingStore.getState().fetchSignals()
  }, [])

  const polling = usePolling(pollFn, SCALING_POLL_INTERVAL, { skipIfFresh })
  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  // WebSocket bindings.
  const bindings: ChannelBinding[] = useMemo(
    () =>
      SCALING_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useScalingStore.getState().updateFromWsEvent(event)
          markFresh()
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    strategies,
    decisions,
    signals,
    totalDecisions,
    loading,
    error,
    evaluating,
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
    evaluateNow,
  }
}

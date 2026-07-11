import { useCallback, useEffect, useMemo, useRef } from 'react'

import type { Plan } from '@/api/types/plans'
import type { WsChannel } from '@/api/types/websocket'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { usePolling } from '@/hooks/usePolling'
import { type ChannelBinding, useWebSocket } from '@/hooks/useWebSocket'
import { usePlansStore } from '@/stores/plans'

const PLANS_POLL_INTERVAL = 30_000
const WS_DEBOUNCE_MS = 300
const PLAN_CHANNELS = ['plans'] as const satisfies readonly WsChannel[]

export interface UsePlansDataReturn {
  plans: readonly Plan[]
  filteredPlans: readonly Plan[]
  totalPlans: number
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function usePlansData(): UsePlansDataReturn {
  const plans = usePlansStore((s) => s.plans)
  const totalPlans = plans.length
  const loading = usePlansStore((s) => s.listLoading)
  const error = usePlansStore((s) => s.listError)
  const statusFilter = usePlansStore((s) => s.statusFilter)

  useEffect(() => {
    void usePlansStore.getState().fetchPlans()
  }, [])

  const { skipIfFresh, markFresh } = useFreshnessGate()
  const pollFn = useCallback(async () => {
    await usePlansStore.getState().fetchPlans()
  }, [])
  const polling = usePolling(pollFn, PLANS_POLL_INTERVAL, { skipIfFresh })

  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  const wsDebounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
    },
    [],
  )

  const bindings: ChannelBinding[] = useMemo(
    () =>
      PLAN_CHANNELS.map((channel) => ({
        channel,
        handler: () => {
          if (wsDebounceRef.current) clearTimeout(wsDebounceRef.current)
          wsDebounceRef.current = setTimeout(() => {
            void usePlansStore
              .getState()
              .fetchPlans()
              .then(() => {
                markFresh()
              })
          }, WS_DEBOUNCE_MS)
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  const filteredPlans = useMemo(() => {
    if (!statusFilter) return plans
    return plans.filter((p) => p.status === statusFilter)
  }, [plans, statusFilter])

  return {
    plans,
    filteredPlans,
    totalPlans,
    loading,
    error,
    wsConnected,
    wsSetupError,
  }
}

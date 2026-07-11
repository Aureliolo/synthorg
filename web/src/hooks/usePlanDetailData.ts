import { useEffect, useMemo } from 'react'

import type { Plan } from '@/api/types'
import type { WsChannel } from '@/api/types/websocket'
import { type ChannelBinding, useWebSocket } from '@/hooks/useWebSocket'
import { usePlansStore } from '@/stores/plans'

const PLAN_CHANNELS = ['plans'] as const satisfies readonly WsChannel[]

export interface UsePlanDetailDataReturn {
  plan: Plan | null
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function usePlanDetailData(planId: string | undefined): UsePlanDetailDataReturn {
  const plan = usePlansStore((s) => s.selectedPlan)
  const loading = usePlansStore((s) => s.detailLoading)
  const error = usePlansStore((s) => s.detailError)

  useEffect(() => {
    if (!planId) return
    void usePlansStore.getState().fetchPlanDetail(planId)
    return () => {
      usePlansStore.getState().clearDetail()
    }
  }, [planId])

  const bindings: ChannelBinding[] = useMemo(
    () =>
      PLAN_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          usePlansStore.getState().updateFromWsEvent(event)
        },
      })),
    [],
  )
  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({ bindings })

  return { plan, loading, error, wsConnected, wsSetupError }
}

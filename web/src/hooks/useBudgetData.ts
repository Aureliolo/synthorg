import { useCallback, useEffect, useMemo } from 'react'
import { useBudgetStore } from '@/stores/budget'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type {
  ActivityItem,
  ForecastResponse,
  OverviewMetrics,
  TrendsResponse,
} from '@/api/types/analytics'
import type { BudgetConfig, CostRecord } from '@/api/types/budget'
import type { WsChannel, WsEvent } from '@/api/types/websocket'
import type { AggregationPeriod } from '@/utils/budget'

const BUDGET_POLL_INTERVAL = 30_000
const BUDGET_CHANNELS = ['budget'] as const satisfies readonly WsChannel[]

export interface UseBudgetDataReturn {
  budgetConfig: BudgetConfig | null
  overview: OverviewMetrics | null
  forecast: ForecastResponse | null
  costRecords: readonly CostRecord[]
  trends: TrendsResponse | null
  activities: readonly ActivityItem[]
  agentNameMap: ReadonlyMap<string, string>
  agentDeptMap: ReadonlyMap<string, string>
  aggregationPeriod: AggregationPeriod
  setAggregationPeriod: (period: AggregationPeriod) => void
  loading: boolean
  error: string | null
  pollingError: string | null
  isRefetching: boolean
  wsConnected: boolean
  wsSetupError: string | null
}

export function useBudgetData(): UseBudgetDataReturn {
  // Zustand individual field selectors -- each subscriber re-renders only when its specific field changes
  const budgetConfig = useBudgetStore((s) => s.budgetConfig)
  const overview = useBudgetStore((s) => s.overview)
  const forecast = useBudgetStore((s) => s.forecast)
  const costRecords = useBudgetStore((s) => s.costRecords)
  const trends = useBudgetStore((s) => s.trends)
  const activities = useBudgetStore((s) => s.activities)
  const agentNameMap = useBudgetStore((s) => s.agentNameMap)
  const agentDeptMap = useBudgetStore((s) => s.agentDeptMap)
  const aggregationPeriod = useBudgetStore((s) => s.aggregationPeriod)
  const setAggregationPeriod = useBudgetStore((s) => s.setAggregationPeriod)
  const loading = useBudgetStore((s) => s.loading)
  const error = useBudgetStore((s) => s.error)

  // Initial fetch on mount
  useEffect(() => {
    void useBudgetStore.getState().fetchBudgetData()
  }, [])

  // Polling for lightweight overview refresh
  const pollFn = useCallback(async () => {
    await useBudgetStore.getState().fetchOverview()
  }, [])
  const polling = usePolling(pollFn, BUDGET_POLL_INTERVAL)
  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  // WebSocket bindings
  const bindings: ChannelBinding[] = useMemo(
    () =>
      BUDGET_CHANNELS.map((channel) => ({
        channel,
        handler: (event: WsEvent) => {
          useBudgetStore.getState().updateFromWsEvent(event)
        },
      })),
    [],
  )
  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    budgetConfig,
    overview,
    forecast,
    costRecords,
    trends,
    activities,
    agentNameMap,
    agentDeptMap,
    aggregationPeriod,
    setAggregationPeriod,
    loading,
    error,
    pollingError: polling.error,
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
  }
}

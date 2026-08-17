import { useCallback, useEffect, useMemo } from 'react'
import { useAnalyticsStore } from '@/stores/analytics'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import type {
  ActivityItem,
  DepartmentHealth,
  ForecastResponse,
  OverviewMetrics,
} from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import type { WsChannel } from '@/api/types/websocket'

const DASHBOARD_POLL_INTERVAL = 30_000
const DASHBOARD_CHANNELS = ['tasks', 'agents', 'budget', 'system', 'approvals'] as const satisfies readonly WsChannel[]

export interface UseDashboardDataReturn {
  overview: OverviewMetrics | null
  forecast: ForecastResponse | null
  departmentHealths: readonly DepartmentHealth[]
  /** How many departments exist, whatever their health read returned. */
  departmentCount: number
  activities: readonly ActivityItem[]
  budgetConfig: BudgetConfig | null
  orgHealthPercent: number | null
  loading: boolean
  error: string | null
  isRefetching: boolean
  wsConnected: boolean
  wsSetupError: string | null
}

export function useDashboardData(): UseDashboardDataReturn {
  const overview = useAnalyticsStore((s) => s.overview)
  const forecast = useAnalyticsStore((s) => s.forecast)
  const departmentHealths = useAnalyticsStore((s) => s.departmentHealths)
  const departmentCount = useAnalyticsStore((s) => s.departmentCount)
  const activities = useAnalyticsStore((s) => s.activities)
  const budgetConfig = useAnalyticsStore((s) => s.budgetConfig)
  const orgHealthPercent = useAnalyticsStore((s) => s.orgHealthPercent)
  const loading = useAnalyticsStore((s) => s.loading)
  const error = useAnalyticsStore((s) => s.error)

  // Initial data fetch
  useEffect(() => {
    void useAnalyticsStore.getState().fetchDashboardData()
  }, [])

  // The shared gate, not a local timestamp: a WS frame only ever adds or
  // updates a row, and the REST refetch is the only thing that reconciles, so
  // an unbounded skip lets continuous activity suppress fetchOverview for as
  // long as the events keep coming.
  const { skipIfFresh, markFresh } = useFreshnessGate()

  // Lightweight polling for overview refresh
  const pollFn = useCallback(async () => {
    await useAnalyticsStore.getState().fetchOverview()
  }, [])
  const polling = usePolling(pollFn, DASHBOARD_POLL_INTERVAL, { skipIfFresh })

  // start/stop are stable refs from useCallback inside usePolling
  const { start, stop } = polling
  useEffect(() => {
    start()
    return () => stop()
  }, [start, stop])

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      DASHBOARD_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          markFresh()
          useAnalyticsStore.getState().updateFromWsEvent(event)
        },
      })),
    [markFresh],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    overview,
    forecast,
    departmentHealths,
    departmentCount,
    activities,
    budgetConfig,
    orgHealthPercent,
    loading,
    error,
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
  }
}

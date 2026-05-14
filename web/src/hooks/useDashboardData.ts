import { useCallback, useEffect, useMemo } from 'react'
import { useAnalyticsStore } from '@/stores/analytics'
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
  activities: readonly ActivityItem[]
  budgetConfig: BudgetConfig | null
  orgHealthPercent: number | null
  loading: boolean
  error: string | null
  wsConnected: boolean
  wsSetupError: string | null
}

export function useDashboardData(): UseDashboardDataReturn {
  const overview = useAnalyticsStore((s) => s.overview)
  const forecast = useAnalyticsStore((s) => s.forecast)
  const departmentHealths = useAnalyticsStore((s) => s.departmentHealths)
  const activities = useAnalyticsStore((s) => s.activities)
  const budgetConfig = useAnalyticsStore((s) => s.budgetConfig)
  const orgHealthPercent = useAnalyticsStore((s) => s.orgHealthPercent)
  const loading = useAnalyticsStore((s) => s.loading)
  const error = useAnalyticsStore((s) => s.error)

  // Initial data fetch
  useEffect(() => {
    void useAnalyticsStore.getState().fetchDashboardData()
  }, [])

  // Lightweight polling for overview refresh
  const pollFn = useCallback(async () => {
    await useAnalyticsStore.getState().fetchOverview()
  }, [])
  const polling = usePolling(pollFn, DASHBOARD_POLL_INTERVAL)

  // polling.start/stop are stable refs from useCallback inside usePolling
  useEffect(() => {
    polling.start()
    return () => polling.stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])

  // WebSocket bindings for real-time updates
  const bindings: ChannelBinding[] = useMemo(
    () =>
      DASHBOARD_CHANNELS.map((channel) => ({
        channel,
        handler: (event) => {
          useAnalyticsStore.getState().updateFromWsEvent(event)
        },
      })),
    [],
  )

  const { connected: wsConnected, setupError: wsSetupError } = useWebSocket({
    bindings,
  })

  return {
    overview,
    forecast,
    departmentHealths,
    activities,
    budgetConfig,
    orgHealthPercent,
    loading,
    error,
    wsConnected,
    wsSetupError,
  }
}

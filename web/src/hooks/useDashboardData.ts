import { useCallback, useEffect, useMemo, useRef } from 'react'
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
/**
 * Window after a WebSocket update during which the scheduled poll
 * skips its fetch. Shorter than the poll interval so a sluggish WS
 * still results in eventual freshness via REST; long enough that a
 * burst of WS events does not trigger redundant polling.
 */
const DASHBOARD_FRESHNESS_WINDOW_MS = 15_000
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
  isRefetching: boolean
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

  // Track the most recent WS-driven update so the scheduled poll can
  // skip when state is fresh enough that re-fetching is wasted work.
  const lastWsUpdateAtRef = useRef<number>(0)

  // Lightweight polling for overview refresh
  const pollFn = useCallback(async () => {
    await useAnalyticsStore.getState().fetchOverview()
  }, [])
  const skipIfFresh = useCallback(
    () => Date.now() - lastWsUpdateAtRef.current < DASHBOARD_FRESHNESS_WINDOW_MS,
    [],
  )
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
          lastWsUpdateAtRef.current = Date.now()
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
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
  }
}

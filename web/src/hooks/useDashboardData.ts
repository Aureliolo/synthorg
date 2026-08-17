import { useCallback, useEffect, useMemo } from 'react'
import { useAnalyticsStore } from '@/stores/analytics'
import { useMissionControlStore } from '@/stores/mission-control'
import { useOrgPulseStore } from '@/stores/org-pulse'
import { useFreshnessGate } from '@/hooks/useFreshnessGate'
import { useWebSocket, type ChannelBinding } from '@/hooks/useWebSocket'
import { usePolling } from '@/hooks/usePolling'
import { computeBlockers, computeQueue, type Blocker, type PulseQueue } from '@/utils/org-pulse'
import type {
  ActivityItem,
  ForecastResponse,
  OverviewMetrics,
} from '@/api/types/analytics'
import type { AgentActivity } from '@/api/types/cockpit'
import type { BudgetConfig } from '@/api/types/budget'
import type { WsChannel } from '@/api/types/websocket'

const DASHBOARD_POLL_INTERVAL = 30_000
const DASHBOARD_CHANNELS = ['tasks', 'agents', 'budget', 'system', 'approvals'] as const satisfies readonly WsChannel[]

export interface UseDashboardDataReturn {
  overview: OverviewMetrics | null
  forecast: ForecastResponse | null
  activities: readonly ActivityItem[]
  budgetConfig: BudgetConfig | null
  /** Work being executed right now, for the pulse panel. */
  running: readonly AgentActivity[]
  queue: PulseQueue
  /** Everything standing between the org and progress, worst first. */
  blockers: readonly Blocker[]
  /** Why each half of the pulse panel cannot be trusted, when it cannot. */
  runningError: string | null
  blockersError: string | null
  runningLoading: boolean
  blockersLoading: boolean
  loading: boolean
  error: string | null
  isRefetching: boolean
  wsConnected: boolean
  wsSetupError: string | null
}

export function useDashboardData(): UseDashboardDataReturn {
  const overview = useAnalyticsStore((s) => s.overview)
  const forecast = useAnalyticsStore((s) => s.forecast)
  const activities = useAnalyticsStore((s) => s.activities)
  const budgetConfig = useAnalyticsStore((s) => s.budgetConfig)
  const loading = useAnalyticsStore((s) => s.loading)
  const error = useAnalyticsStore((s) => s.error)
  const snapshot = useMissionControlStore((s) => s.snapshot)
  const snapshotError = useMissionControlStore((s) => s.snapshotError)
  const snapshotLoading = useMissionControlStore((s) => s.snapshotLoading)
  const subsystems = useOrgPulseStore((s) => s.subsystems)
  const blockedTasks = useOrgPulseStore((s) => s.blockedTasks)
  const subsystemsError = useOrgPulseStore((s) => s.subsystemsError)
  const blockedTasksError = useOrgPulseStore((s) => s.blockedTasksError)
  const pulseLoading = useOrgPulseStore((s) => s.loading)

  // Initial data fetch. The pulse reads run alongside the analytics ones rather
  // than after them: neither depends on the other, and a slow subsystem probe
  // must not hold the metric cards back.
  useEffect(() => {
    void useAnalyticsStore.getState().fetchDashboardData()
    void useOrgPulseStore.getState().fetchOrgPulse()
    void useMissionControlStore.getState().fetchSnapshot()
  }, [])

  // The shared gate, not a local timestamp: a WS frame only ever adds or
  // updates a row, and the REST refetch is the only thing that reconciles, so
  // an unbounded skip lets continuous activity suppress fetchOverview for as
  // long as the events keep coming.
  const { skipIfFresh, markFresh } = useFreshnessGate()

  // Lightweight polling for overview refresh. The pulse rides the dashboard's
  // own 30s cadence rather than the cockpit page's 5s one: a summary panel does
  // not need per-turn resolution, and three requests every five seconds would be
  // a real cost for a page nobody is watching that closely.
  // Settled together, not awaited in sequence: the three reads are independent,
  // so serialising them makes each tick cost their sum for no ordering benefit,
  // and one slow read would delay the other two.
  const pollFn = useCallback(async () => {
    await Promise.allSettled([
      useAnalyticsStore.getState().fetchOverview(),
      useOrgPulseStore.getState().fetchOrgPulse(),
      useMissionControlStore.getState().fetchSnapshot(),
    ])
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

  const blockers = useMemo(
    () => computeBlockers({ overview, blockedTasks, subsystems }),
    [overview, blockedTasks, subsystems],
  )
  const queue = useMemo(() => computeQueue(overview), [overview])

  return {
    overview,
    forecast,
    activities,
    budgetConfig,
    running: snapshot?.agents ?? [],
    queue,
    blockers,
    // Each half of the pulse panel reports the read that feeds it. A failed
    // fetch must never reach the panel as an empty list, because both halves
    // make a positive claim about the org when their list is empty.
    runningError: snapshotError,
    blockersError: subsystemsError ?? blockedTasksError,
    runningLoading: snapshotLoading,
    blockersLoading: pulseLoading,
    loading,
    error,
    isRefetching: polling.isRefetching,
    wsConnected,
    wsSetupError,
  }
}

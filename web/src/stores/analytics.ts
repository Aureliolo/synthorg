import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import { getOverviewMetrics, getForecast } from '@/api/endpoints/analytics'
import { getBudgetConfig } from '@/api/endpoints/budget'
import { listActivities } from '@/api/endpoints/activities'
import { wsEventToActivityItem } from '@/utils/dashboard'
import { deepEqual } from '@/utils/equality'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type {
  ActivityItem,
  ForecastResponse,
  OverviewMetrics,
} from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('analytics')

const MAX_ACTIVITIES = 50

interface AnalyticsState {
  overview: OverviewMetrics | null
  forecast: ForecastResponse | null
  activities: readonly ActivityItem[]
  budgetConfig: BudgetConfig | null
  loading: boolean
  error: string | null
  fetchDashboardData: () => Promise<void>
  fetchOverview: () => Promise<void>
  pushActivity: (item: ActivityItem) => void
  updateFromWsEvent: (event: WsEvent) => void
}

type AnSet = StoreApi<AnalyticsState>['setState']

interface DashboardResults {
  overview: OverviewMetrics | null
  forecast: ForecastResponse | null
  budgetConfig: BudgetConfig | null
  activitiesData: readonly ActivityItem[]
  failureReason: unknown
}

async function fetchAllDashboardEndpoints(): Promise<DashboardResults> {
  const [overviewResult, forecastResult, budgetResult, activitiesResult] =
    await Promise.allSettled([
      getOverviewMetrics(),
      getForecast(),
      getBudgetConfig(),
      listActivities({ limit: 20 }),
    ])
  return {
    overview: overviewResult.status === 'fulfilled'
      ? overviewResult.value
      : null,
    forecast: forecastResult.status === 'fulfilled'
      ? forecastResult.value
      : null,
    budgetConfig: budgetResult.status === 'fulfilled'
      ? budgetResult.value
      : null,
    activitiesData: activitiesResult.status === 'fulfilled'
      ? activitiesResult.value.data
      : [],
    failureReason: overviewResult.status === 'rejected'
      ? overviewResult.reason
      : null,
  }
}

async function fetchDashboardDataImpl(set: AnSet): Promise<void> {
  set({ loading: true, error: null })
  try {
    const results = await fetchAllDashboardEndpoints()
    if (!results.overview) {
      set({
        loading: false,
        error: getErrorMessage(
          results.failureReason ?? 'Failed to load overview',
        ),
      })
      return
    }
    set((state) => {
      // WS events pushed while this fetch was in flight are newer than
      // the fetched snapshot; overwriting would silently drop them from
      // the live feed. Keep them ahead of the fetched history.
      const fetchedIds = new Set(results.activitiesData.map((a) => a.id))
      const liveDuringFetch = state.activities.filter(
        (a) => !fetchedIds.has(a.id),
      )
      return {
        overview: results.overview,
        forecast: results.forecast,
        budgetConfig: results.budgetConfig,
        activities: [...liveDuringFetch, ...results.activitiesData].slice(
          0,
          MAX_ACTIVITIES,
        ),
        loading: false,
        error: null,
      }
    })
  } catch (err) {
    set({ loading: false, error: getErrorMessage(err) })
  }
}

export const useAnalyticsStore = create<AnalyticsState>()((set, get) => ({
  overview: null,
  forecast: null,
  activities: [],
  budgetConfig: null,
  loading: false,
  error: null,

  fetchDashboardData: () => fetchDashboardDataImpl(set),

  fetchOverview: async () => {
    try {
      const overview = await getOverviewMetrics()
      // Unchanged data keeps the existing reference so subscribers
      // (charts, gauges, status bar) skip a full re-render wave on
      // every idle poll tick.
      if (!deepEqual(overview, get().overview)) {
        set({ overview })
      }
    } catch (err) {
      log.warn(
        'Failed to refresh overview (polling):',
        getErrorMessage(err),
      )
    }
  },

  pushActivity: (item) => {
    set((state) => ({
      activities: [item, ...state.activities].slice(0, MAX_ACTIVITIES),
    }))
  },

  updateFromWsEvent: (event) => {
    try {
      const item = wsEventToActivityItem(event)
      get().pushActivity(item)
    } catch (err) {
      log.error('Failed to process WebSocket event:', err)
    }
  },
}))

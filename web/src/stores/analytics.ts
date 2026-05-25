import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import { getOverviewMetrics, getForecast } from '@/api/endpoints/analytics'
import { getBudgetConfig } from '@/api/endpoints/budget'
import { listDepartments, getDepartmentHealth } from '@/api/endpoints/company'
import { listActivities } from '@/api/endpoints/activities'
import { computeOrgHealth, wsEventToActivityItem } from '@/utils/dashboard'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type {
  ActivityItem,
  DepartmentHealth,
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
  departmentHealths: readonly DepartmentHealth[]
  activities: readonly ActivityItem[]
  budgetConfig: BudgetConfig | null
  orgHealthPercent: number | null
  loading: boolean
  error: string | null
  fetchDashboardData: () => Promise<void>
  fetchOverview: () => Promise<void>
  pushActivity: (item: ActivityItem) => void
  updateFromWsEvent: (event: WsEvent) => void
}

type AnSet = StoreApi<AnalyticsState>['setState']

async function fetchDepartmentHealths(): Promise<DepartmentHealth[]> {
  try {
    const deptResult = await listDepartments({ limit: 100 })
    const healthPromises = deptResult.data.map((dept) =>
      getDepartmentHealth(dept.name).catch((err: unknown) => {
        log.warn('Failed to fetch health for dept:', dept.name, err)
        return null
      }),
    )
    const healthResults = await Promise.all(healthPromises)
    return healthResults.filter(
      (h): h is DepartmentHealth => h !== null,
    )
  } catch (err) {
    log.warn('Failed to fetch department list:', getErrorMessage(err))
    return []
  }
}

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
    const departmentHealths = await fetchDepartmentHealths()
    set({
      overview: results.overview,
      forecast: results.forecast,
      budgetConfig: results.budgetConfig,
      departmentHealths,
      orgHealthPercent: computeOrgHealth(departmentHealths),
      activities: results.activitiesData,
      loading: false,
      error: null,
    })
  } catch (err) {
    set({ loading: false, error: getErrorMessage(err) })
  }
}

export const useAnalyticsStore = create<AnalyticsState>()((set, get) => ({
  overview: null,
  forecast: null,
  departmentHealths: [],
  activities: [],
  budgetConfig: null,
  orgHealthPercent: null,
  loading: false,
  error: null,

  fetchDashboardData: () => fetchDashboardDataImpl(set),

  fetchOverview: async () => {
    try {
      const overview = await getOverviewMetrics()
      set({ overview })
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

import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import { paginateAll } from '@/api/client'
import { getOverviewMetrics, getForecast } from '@/api/endpoints/analytics'
import { getBudgetConfig } from '@/api/endpoints/budget'
import { listDepartments, listDepartmentHealth } from '@/api/endpoints/company'
import { listActivities } from '@/api/endpoints/activities'
import { wsEventToActivityItem } from '@/utils/dashboard'
import { deepEqual } from '@/utils/equality'
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
  /**
   * How many departments the org has, which is not the length of the list
   * above: a health read that fails leaves the org's departments intact and
   * their healths unknown, and only the count can tell the panel apart from
   * an org with none.
   */
  departmentCount: number
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

/**
 * The org's departments and their health, as two separately-knowable facts.
 *
 * They were one. Every health read that failed was dropped and the survivors
 * returned, so a fleet-wide refusal and an org with no departments produced
 * the same empty list, and the panel told an operator with six departments to
 * go and set their organisation up.
 */
interface DepartmentHealthSnapshot {
  readonly healths: readonly DepartmentHealth[]
  /** How many departments exist, whatever their health read returned. */
  readonly departmentCount: number
}

const NO_DEPARTMENTS: DepartmentHealthSnapshot = {
  healths: [],
  departmentCount: 0,
}

/** Page size for the fallback department walk that counts them all. */
const DEPARTMENT_PAGE_SIZE = 100

async function fetchDepartmentHealths(): Promise<DepartmentHealthSnapshot> {
  try {
    // One read for the whole org: asking per department cost one request per
    // row against a per-operation budget, and the refusals rendered as an
    // unconfigured org.
    const healths = await listDepartmentHealth()
    return { healths, departmentCount: healths.length }
  } catch (err) {
    log.warn('Failed to fetch department health:', getErrorMessage(err))
  }
  // The health read failed, so the count has to come from somewhere else
  // before the panel can tell "no departments" from "health unavailable".
  try {
    // Every page, not the first: a count is the whole point of this read, and
    // one page of it is a number that happens to equal the page size.
    const departments = await paginateAll((cursor) =>
      listDepartments({
        limit: DEPARTMENT_PAGE_SIZE,
        ...(cursor ? { cursor } : {}),
      }),
    )
    return { healths: [], departmentCount: departments.length }
  } catch (err) {
    log.warn('Failed to fetch department list:', getErrorMessage(err))
    return NO_DEPARTMENTS
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
    const snapshot = await fetchDepartmentHealths()
    const departmentHealths = snapshot.healths
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
        departmentHealths,
        departmentCount: snapshot.departmentCount,
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
  departmentHealths: [],
  departmentCount: 0,
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

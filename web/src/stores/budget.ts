import type { StoreApi } from 'zustand'
import { create } from 'zustand'
import {
  getOverviewMetrics,
  getTrends,
  getForecast,
} from '@/api/endpoints/analytics'
import { getBudgetConfig, listCostRecords } from '@/api/endpoints/budget'
import { listActivities } from '@/api/endpoints/activities'
import { listAgents } from '@/api/endpoints/agents'
import { wsEventToActivityItem } from '@/utils/dashboard'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { sanitizeWsString } from '@/utils/ws-sanitize'
import { createLogger } from '@/lib/logger'
import { aggregateWeekly, type AggregationPeriod } from '@/utils/budget'
import type {
  ActivityItem,
  ForecastResponse,
  OverviewMetrics,
  TrendsResponse,
} from '@/api/types/analytics'
import type {
  BudgetConfig,
  CostRecord,
  DailySummary,
  PeriodSummary,
} from '@/api/types/budget'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('budget')

const MAX_BUDGET_ACTIVITIES = 30

/** Maps display aggregation granularity to the trends API time-range. */
const PERIOD_TO_API = {
  hourly: '7d',
  daily: '30d',
  weekly: '90d',
} as const satisfies Record<AggregationPeriod, string>

interface BudgetState {
  budgetConfig: BudgetConfig | null
  overview: OverviewMetrics | null
  forecast: ForecastResponse | null
  costRecords: readonly CostRecord[]
  dailySummary: readonly DailySummary[]
  periodSummary: PeriodSummary | null
  trends: TrendsResponse | null
  activities: readonly ActivityItem[]
  agentNameMap: ReadonlyMap<string, string>
  agentDeptMap: ReadonlyMap<string, string>

  aggregationPeriod: AggregationPeriod
  loading: boolean
  error: string | null

  fetchBudgetData: () => Promise<void>
  fetchOverview: () => Promise<void>
  fetchTrends: () => Promise<void>
  setAggregationPeriod: (period: AggregationPeriod) => void
  pushActivity: (item: ActivityItem) => void
  updateFromWsEvent: (event: WsEvent) => void
}

type BudgetSet = StoreApi<BudgetState>['setState']
type BudgetGet = StoreApi<BudgetState>['getState']

interface BudgetFetchResults {
  overview: OverviewMetrics | null
  budgetConfig: BudgetConfig | null
  forecast: ForecastResponse | null
  recordsResult: Awaited<ReturnType<typeof listCostRecords>> | null
  trends: TrendsResponse | null
  activitiesData: readonly ActivityItem[]
  failureReason: unknown
}

function pickFailureReason(
  overviewR: PromiseSettledResult<unknown>,
  budgetR: PromiseSettledResult<unknown>,
): unknown {
  if (overviewR.status === 'rejected') return overviewR.reason
  if (budgetR.status === 'rejected') return budgetR.reason
  return null
}

function valueOrNull<T>(result: PromiseSettledResult<T>): T | null {
  return result.status === 'fulfilled' ? result.value : null
}

async function fetchAllBudgetEndpoints(): Promise<BudgetFetchResults> {
  const [overviewR, budgetR, forecastR, recordsR, trendsR, activitiesR] =
    await Promise.allSettled([
      getOverviewMetrics(),
      getBudgetConfig(),
      getForecast(),
      listCostRecords({ limit: 200 }),
      getTrends('30d', 'spend'),
      listActivities({ limit: 30 }),
    ])
  logBudgetFailures({ forecastR, recordsR, trendsR, activitiesR })
  return {
    overview: valueOrNull(overviewR),
    budgetConfig: valueOrNull(budgetR),
    forecast: valueOrNull(forecastR),
    recordsResult: valueOrNull(recordsR),
    trends: valueOrNull(trendsR),
    activitiesData: activitiesR.status === 'fulfilled'
      ? activitiesR.value.data
      : [],
    failureReason: pickFailureReason(overviewR, budgetR),
  }
}

interface SecondaryRejections {
  forecastR: PromiseSettledResult<ForecastResponse>
  recordsR: PromiseSettledResult<Awaited<ReturnType<typeof listCostRecords>>>
  trendsR: PromiseSettledResult<TrendsResponse>
  activitiesR: PromiseSettledResult<Awaited<ReturnType<typeof listActivities>>>
}

function logBudgetFailures(results: SecondaryRejections): void {
  if (results.forecastR.status === 'rejected') {
    log.warn('Failed to fetch forecast:', sanitizeForLog(results.forecastR.reason))
  }
  if (results.recordsR.status === 'rejected') {
    log.warn('Failed to fetch cost records:', sanitizeForLog(results.recordsR.reason))
  }
  if (results.trendsR.status === 'rejected') {
    log.warn('Failed to fetch trends:', sanitizeForLog(results.trendsR.reason))
  }
  if (results.activitiesR.status === 'rejected') {
    log.warn('Failed to fetch activities:', sanitizeForLog(results.activitiesR.reason))
  }
}

interface AgentMaps {
  agentNameMap: Map<string, string>
  agentDeptMap: Map<string, string>
}

async function fetchAgentMaps(): Promise<AgentMaps> {
  const agentNameMap = new Map<string, string>()
  const agentDeptMap = new Map<string, string>()
  try {
    const agentsResult = await listAgents({ limit: 100 })
    for (const agent of agentsResult.data) {
      const keys = new Set<string>([agent.name])
      if (agent.id) keys.add(agent.id)
      for (const key of keys) {
        agentNameMap.set(key, agent.name)
        agentDeptMap.set(key, agent.department)
      }
    }
  } catch (err) {
    log.warn(
      'Failed to fetch agent list for name/dept mapping:',
      sanitizeForLog(err),
    )
  }
  return { agentNameMap, agentDeptMap }
}

function applyFetchedBudgetData(
  set: BudgetSet,
  fetched: BudgetFetchResults,
  agentNameMap: ReadonlyMap<string, string>,
  agentDeptMap: ReadonlyMap<string, string>,
): void {
  set({
    overview: fetched.overview,
    budgetConfig: fetched.budgetConfig,
    forecast: fetched.forecast,
    costRecords: fetched.recordsResult?.data ?? [],
    dailySummary: fetched.recordsResult?.daily_summary ?? [],
    periodSummary: fetched.recordsResult?.period_summary ?? null,
    trends: fetched.trends,
    activities: fetched.activitiesData,
    agentNameMap,
    agentDeptMap,
    loading: false,
    error: null,
  })
}

async function fetchBudgetDataImpl(set: BudgetSet): Promise<void> {
  set({ loading: true, error: null })
  try {
    const fetched = await fetchAllBudgetEndpoints()
    if (!fetched.overview || !fetched.budgetConfig) {
      set({
        loading: false,
        error: getErrorMessage(
          fetched.failureReason ?? 'Failed to load budget data',
        ),
      })
      return
    }
    const { agentNameMap, agentDeptMap } = await fetchAgentMaps()
    applyFetchedBudgetData(set, fetched, agentNameMap, agentDeptMap)
  } catch (err) {
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchTrendsImpl(set: BudgetSet, get: BudgetGet): Promise<void> {
  const { aggregationPeriod } = get()
  const apiPeriod = PERIOD_TO_API[aggregationPeriod]
  try {
    const result = await getTrends(apiPeriod, 'spend')
    if (aggregationPeriod === 'weekly') {
      set({
        trends: {
          ...result,
          data_points: aggregateWeekly(result.data_points),
        },
      })
    } else {
      set({ trends: result })
    }
  } catch (err) {
    set({ trends: null })
    log.warn('Failed to fetch trends:', sanitizeForLog(err))
  }
}

export const useBudgetStore = create<BudgetState>()((set, get) => ({
  budgetConfig: null,
  overview: null,
  forecast: null,
  costRecords: [],
  dailySummary: [],
  periodSummary: null,
  trends: null,
  activities: [],
  agentNameMap: new Map(),
  agentDeptMap: new Map(),

  aggregationPeriod: 'daily',
  loading: false,
  error: null,

  fetchBudgetData: () => fetchBudgetDataImpl(set),

  fetchOverview: async () => {
    try {
      const overview = await getOverviewMetrics()
      set({ overview })
    } catch (err) {
      log.warn('Failed to refresh overview (polling):', sanitizeForLog(err))
    }
  },

  fetchTrends: () => fetchTrendsImpl(set, get),

  setAggregationPeriod: (period) => {
    set({ aggregationPeriod: period })
    get().fetchTrends().catch(() => {
      // Already handled inside fetchTrends
    })
  },

  pushActivity: (item) => {
    set((state) => ({
      activities: [item, ...state.activities].slice(0, MAX_BUDGET_ACTIVITIES),
    }))
  },

  updateFromWsEvent: (event) => {
    // Sanitize the WS-supplied event_type once so both the dispatch
    // branch and the failure log read the same normalised value.
    const eventType = sanitizeWsString(event.event_type, 64)
    try {
      const item = wsEventToActivityItem(event)
      get().pushActivity(item)
      if (eventType === 'budget.record_added') {
        void get().fetchOverview()
      }
    } catch (err) {
      log.error('Failed to process WS event:', {
        type: eventType,
        channel: sanitizeWsString(event.channel),
        error: sanitizeForLog(err),
      })
    }
  },
}))

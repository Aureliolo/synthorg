import { beforeEach, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import type { ForecastResponse, OverviewMetrics, TrendsResponse } from '@/api/types/analytics'
import type { BudgetConfig, CostRecord } from '@/api/types/budget'
import type { WsEvent } from '@/api/types/websocket'
import { apiError, apiPaginatedError, apiSuccess } from '@/mocks/handlers'
import { server } from '@/test-setup'
import { useBudgetStore } from '@/stores/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const mockOverview: OverviewMetrics = {
  total_tasks: 10,
  tasks_by_status: {},
  total_agents: 5,
  total_cost: 42,
  budget_remaining: 58,
  budget_used_percent: 42,
  cost_7d_trend: [],
  active_agents_count: 3,
  idle_agents_count: 2,
  currency: DEFAULT_CURRENCY,
}

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 100,
  alerts: { warn_at: 75, critical_at: 90, hard_stop_at: 100 },
  per_task_limit: 5,
  per_agent_daily_limit: 20,
  auto_downgrade: {
    enabled: false,
    threshold: 85,
    downgrade_map: [],
    boundary: 'task_assignment',
  },
  reset_day: 1,
  currency: DEFAULT_CURRENCY,
  pte_tracking_enabled: false,
  forecast_required: true,
  forecast_default_ceiling_multiplier: 1.5,
  run_hard_ceiling: 0,
  forecast_static_prior_per_turn_large: 0.1,
  forecast_static_prior_per_turn_medium: 0.03,
  forecast_static_prior_per_turn_small: 0.005,
  forecast_static_prior_per_turn_local_small: 0,
  forecast_shrinkage_prior_weight: 5,
  benchmark_provider: 'stub',
  model_tier_overrides: {},
  risk_budget: {
    alerts: { critical_at: 90, warn_at: 75 },
    enabled: false,
    per_agent_daily_risk_limit: 20,
    per_task_risk_limit: 5,
    total_daily_risk_limit: 100,
  },
}

const mockForecast: ForecastResponse = {
  horizon_days: 14,
  projected_total: 80,
  daily_projections: [],
  days_until_exhausted: 20,
  confidence: 0.8,
  avg_daily_spend: 3,
  currency: DEFAULT_CURRENCY,
}

const mockTrends: TrendsResponse = {
  period: '30d',
  metric: 'spend',
  bucket_size: 'day',
  data_points: [{ timestamp: '2026-03-20', value: 5 }],
}

const mockCostRecord: CostRecord = {
  agent_id: 'a1',
  task_id: 't1',
  project_id: null,
  provider: 'test-provider',
  model: 'test-model-001',
  input_tokens: 100,
  output_tokens: 50,
  cost: 1.0,
  currency: 'USD',
  timestamp: '2026-03-20T10:00:00Z',
  call_category: 'productive',
  accuracy_effort_ratio: null,
  latency_ms: null,
  cache_hit: null,
  retry_count: null,
  retry_reason: null,
  finish_reason: null,
  success: null,
  claim_id: '',
}

const mockAgent = {
  id: 'a1',
  name: 'Alpha',
  role: 'Developer',
  department: 'engineering',
  level: 'mid',
  status: 'active',
  personality: {},
  model: {},
  memory: {},
  tools: {},
  authority: {},
  autonomy_level: 'semi',
  hiring_date: '2026-01-01T00:00:00Z',
}

type HandlerFn = () => Response | Promise<Response>

type FixtureOverrides = Partial<{
  overview: HandlerFn
  budget: HandlerFn
  forecast: HandlerFn
  records: HandlerFn
  trends: HandlerFn
  agents: HandlerFn
  activities: HandlerFn
}>

function installDefaults(overrides: FixtureOverrides = {}) {
  const defaultRecordsBody = {
    success: true,
    data: [mockCostRecord],
    error: null,
    error_detail: null,
    pagination: { total: 1, offset: 0, limit: 500 },
    daily_summary: [],
    period_summary: {
      avg_cost: 1,
      total_cost: 1,
      total_input_tokens: 100,
      total_output_tokens: 50,
      record_count: 1,
      currency: DEFAULT_CURRENCY,
    },
    currency: DEFAULT_CURRENCY,
  }
  const defaultAgentsBody = {
    data: [mockAgent],
    error: null,
    error_detail: null,
    success: true,
    pagination: { total: 1, offset: 0, limit: 100 },
  }
  const defaultActivitiesBody = {
    data: [],
    error: null,
    error_detail: null,
    success: true,
    pagination: { total: 0, offset: 0, limit: 30 },
  }
  server.use(
    http.get('/api/v1/analytics/overview', () =>
      overrides.overview !== undefined
        ? overrides.overview()
        : HttpResponse.json(apiSuccess(mockOverview)),
    ),
    http.get('/api/v1/budget/config', () =>
      overrides.budget !== undefined
        ? overrides.budget()
        : HttpResponse.json(apiSuccess(mockBudgetConfig)),
    ),
    http.get('/api/v1/analytics/forecast', () =>
      overrides.forecast !== undefined
        ? overrides.forecast()
        : HttpResponse.json(apiSuccess(mockForecast)),
    ),
    http.get('/api/v1/budget/records', () =>
      overrides.records !== undefined
        ? overrides.records()
        : HttpResponse.json(defaultRecordsBody),
    ),
    http.get('/api/v1/analytics/trends', () =>
      overrides.trends !== undefined
        ? overrides.trends()
        : HttpResponse.json(apiSuccess(mockTrends)),
    ),
    http.get('/api/v1/activities', () =>
      overrides.activities !== undefined
        ? overrides.activities()
        : HttpResponse.json(defaultActivitiesBody),
    ),
    http.get('/api/v1/agents', () =>
      overrides.agents !== undefined
        ? overrides.agents()
        : HttpResponse.json(defaultAgentsBody),
    ),
  )
}

beforeEach(() => {
  useBudgetStore.setState({
    budgetConfig: null,
    overview: null,
    forecast: null,
    costRecords: [],
    trends: null,
    activities: [],
    agentNameMap: new Map(),
    agentDeptMap: new Map(),
    aggregationPeriod: 'daily',
    loading: false,
    error: null,
  })
})

describe('fetchBudgetData', () => {
  it('sets loading to true at the start of fetch', () => {
    installDefaults()
    void useBudgetStore.getState().fetchBudgetData()
    expect(useBudgetStore.getState().loading).toBe(true)
  })

  it('populates all state fields on success', async () => {
    installDefaults()
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.overview).toEqual(mockOverview)
    expect(state.budgetConfig).toEqual(mockBudgetConfig)
    expect(state.forecast).toEqual(mockForecast)
    expect(state.costRecords).toHaveLength(1)
    expect(state.trends).toEqual(mockTrends)
    expect(state.loading).toBe(false)
    expect(state.error).toBeNull()
  })

  it('builds agentNameMap and agentDeptMap from agent list', async () => {
    installDefaults()
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.agentNameMap.get('a1')).toBe('Alpha')
    expect(state.agentDeptMap.get('a1')).toBe('engineering')
  })

  it('sets error when getOverviewMetrics fails', async () => {
    installDefaults({
      overview: () => HttpResponse.json(apiError('overview down')),
    })
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.error).toBe('overview down')
    expect(state.loading).toBe(false)
  })

  it('sets error when getBudgetConfig fails', async () => {
    installDefaults({
      budget: () => HttpResponse.json(apiError('config down')),
    })
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.error).toBe('config down')
    expect(state.loading).toBe(false)
  })

  it('degrades gracefully when getForecast fails', async () => {
    installDefaults({
      forecast: () => HttpResponse.json(apiError('no forecast')),
    })
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.forecast).toBeNull()
    expect(state.error).toBeNull()
  })

  it('degrades gracefully when listCostRecords fails', async () => {
    installDefaults({
      records: () => HttpResponse.json(apiError('no records')),
    })
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.costRecords).toEqual([])
    expect(state.error).toBeNull()
  })

  it('degrades gracefully when listAgents fails', async () => {
    installDefaults({
      agents: () => HttpResponse.json(apiPaginatedError('agents down')),
    })
    await useBudgetStore.getState().fetchBudgetData()
    const state = useBudgetStore.getState()
    expect(state.agentNameMap.size).toBe(0)
    expect(state.agentDeptMap.size).toBe(0)
    expect(state.error).toBeNull()
  })
})

describe('fetchOverview', () => {
  it('updates overview without resetting other fields', async () => {
    useBudgetStore.setState({ forecast: mockForecast })
    server.use(
      http.get('/api/v1/analytics/overview', () =>
        HttpResponse.json(apiSuccess(mockOverview)),
      ),
    )
    await useBudgetStore.getState().fetchOverview()
    const state = useBudgetStore.getState()
    expect(state.overview).toEqual(mockOverview)
    expect(state.forecast).toEqual(mockForecast)
  })

  it('does not set error state when fetchOverview fails', async () => {
    server.use(
      http.get('/api/v1/analytics/overview', () =>
        HttpResponse.json(apiError('network')),
      ),
    )
    await useBudgetStore.getState().fetchOverview()
    expect(useBudgetStore.getState().error).toBeNull()
    expect(useBudgetStore.getState().loading).toBe(false)
  })
})

describe('fetchTrends', () => {
  it('maps daily period to 30d API call', async () => {
    useBudgetStore.setState({ aggregationPeriod: 'daily' })
    let capturedPeriod: string | null = null
    let capturedMetric: string | null = null
    server.use(
      http.get('/api/v1/analytics/trends', ({ request }) => {
        const params = new URL(request.url).searchParams
        capturedPeriod = params.get('period')
        capturedMetric = params.get('metric')
        return HttpResponse.json(apiSuccess(mockTrends))
      }),
    )
    await useBudgetStore.getState().fetchTrends()
    expect(capturedPeriod).toBe('30d')
    expect(capturedMetric).toBe('spend')
  })

  it('maps hourly period to 7d API call', async () => {
    useBudgetStore.setState({ aggregationPeriod: 'hourly' })
    let capturedPeriod: string | null = null
    server.use(
      http.get('/api/v1/analytics/trends', ({ request }) => {
        capturedPeriod = new URL(request.url).searchParams.get('period')
        return HttpResponse.json(apiSuccess(mockTrends))
      }),
    )
    await useBudgetStore.getState().fetchTrends()
    expect(capturedPeriod).toBe('7d')
  })

  it('clears trends on fetchTrends failure', async () => {
    useBudgetStore.setState({ trends: mockTrends })
    server.use(
      http.get('/api/v1/analytics/trends', () =>
        HttpResponse.json(apiError('network')),
      ),
    )
    await useBudgetStore.getState().fetchTrends()
    expect(useBudgetStore.getState().trends).toBeNull()
  })

  it('maps weekly period to 90d API call and aggregates', async () => {
    useBudgetStore.setState({ aggregationPeriod: 'weekly' })
    let capturedPeriod: string | null = null
    server.use(
      http.get('/api/v1/analytics/trends', ({ request }) => {
        capturedPeriod = new URL(request.url).searchParams.get('period')
        return HttpResponse.json(
          apiSuccess({
            ...mockTrends,
            period: '90d',
            data_points: [
              { timestamp: '2026-03-23', value: 3 },
              { timestamp: '2026-03-24', value: 7 },
            ],
          }),
        )
      }),
    )
    await useBudgetStore.getState().fetchTrends()
    expect(capturedPeriod).toBe('90d')
    const state = useBudgetStore.getState()
    expect(state.trends!.data_points).toHaveLength(1)
    expect(state.trends!.data_points[0]!.value).toBe(10)
  })
})

describe('setAggregationPeriod', () => {
  it('updates period in state', () => {
    server.use(
      http.get('/api/v1/analytics/trends', () =>
        HttpResponse.json(apiSuccess(mockTrends)),
      ),
    )
    useBudgetStore.getState().setAggregationPeriod('hourly')
    expect(useBudgetStore.getState().aggregationPeriod).toBe('hourly')
  })

  it('calls fetchTrends when period changes', async () => {
    let capturedPeriod: string | null = null
    server.use(
      http.get('/api/v1/analytics/trends', ({ request }) => {
        capturedPeriod = new URL(request.url).searchParams.get('period')
        return HttpResponse.json(apiSuccess(mockTrends))
      }),
    )
    useBudgetStore.getState().setAggregationPeriod('weekly')
    await vi.waitFor(() => {
      expect(capturedPeriod).toBe('90d')
    })
  })
})

describe('pushActivity', () => {
  it('prepends and caps at 30', () => {
    const existing = Array.from({ length: 30 }, (_, i) => ({
      id: `old-${i}`,
      timestamp: '2026-03-20T10:00:00Z',
      agent_name: 'Bot',
      action_type: 'budget.record_added' as const,
      description: 'recorded a cost',
      task_id: null,
      department: null,
    }))
    useBudgetStore.setState({ activities: existing })
    useBudgetStore.getState().pushActivity({
      id: 'new',
      timestamp: '2026-03-20T11:00:00Z',
      agent_name: 'Bot',
      action_type: 'budget.alert',
      description: 'alert',
      task_id: null,
      department: null,
    })
    const { activities } = useBudgetStore.getState()
    expect(activities).toHaveLength(30)
    expect(activities[0]!.id).toBe('new')
  })
})

describe('updateFromWsEvent', () => {
  it('converts event to activity and pushes it', () => {
    const event: WsEvent = {
      event_type: 'budget.record_added',
      channel: 'budget',
      timestamp: '2026-03-20T10:00:00Z',
      payload: { agent_name: 'CFO Bot' },
    }
    useBudgetStore.getState().updateFromWsEvent(event)
    const { activities } = useBudgetStore.getState()
    expect(activities).toHaveLength(1)
    expect(activities[0]!.agent_name).toBe('CFO Bot')
  })

  it('triggers fetchOverview when event_type is budget.record_added', async () => {
    let overviewCalls = 0
    server.use(
      http.get('/api/v1/analytics/overview', () => {
        overviewCalls += 1
        return HttpResponse.json(apiSuccess(mockOverview))
      }),
    )
    const event: WsEvent = {
      event_type: 'budget.record_added',
      channel: 'budget',
      timestamp: '2026-03-20T10:00:00Z',
      payload: { agent_name: 'CFO Bot' },
    }
    useBudgetStore.getState().updateFromWsEvent(event)
    await vi.waitFor(() => {
      expect(overviewCalls).toBeGreaterThan(0)
    })
  })
})

import { http, HttpResponse } from 'msw'
import type {
  CostRecordListResponseBody,
  approveForecast,
  createForecast,
  getAgentSpending,
  getBudgetConfig,
  getCallAnalytics,
  getForecast,
  getParetoFrontier,
  getPromptClassBreakdown,
  raiseCeiling,
  rejectForecast,
} from '@/api/endpoints/budget'
import type {
  AgentSpending,
  AnalyticsAggregation,
  BudgetConfig,
  ForecastView,
  ParetoFrontier,
  PromptClassBreakdown,
} from '@/api/types/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { successFor } from './helpers'

function buildForecast(overrides: Partial<ForecastView> = {}): ForecastView {
  return {
    forecast_id: '00000000-0000-0000-0000-000000000001',
    brief_hash: 'a'.repeat(64),
    estimated_cost: 0.85,
    lower_bound: 0.55,
    upper_bound: 1.15,
    currency: DEFAULT_CURRENCY,
    decision: 'pending',
    decided_at: null,
    decided_by: null,
    ceiling_amount: null,
    halt_context: null,
    created_at: '2026-05-20T12:00:00Z',
    updated_at: '2026-05-20T12:00:00Z',
    ...overrides,
  }
}

function buildParetoFrontier(): ParetoFrontier {
  return {
    points: [],
    source: 'no-measured-scores',
    generated_at: '2026-05-20T12:00:00Z',
    baseline_window_size: 0,
  }
}

function buildCallAnalytics(): AnalyticsAggregation {
  return {
    total_calls: 0,
    success_count: 0,
    failure_count: 0,
    unreported_count: 0,
    // Null rather than 0: nothing reported an outcome, which is a different
    // fact from every call having failed.
    success_rate: null,
    retry_count: 0,
    retry_rate: 0,
    cached_input_tokens: 0,
    cached_input_share: null,
    avg_latency_ms: null,
    p95_latency_ms: null,
    by_finish_reason: [],
    orchestration_ratio: {
      alert_level: 'normal',
      coordination_tokens: 0,
      productive_tokens: 0,
      ratio: 0,
      system_tokens: 0,
      total_tokens: 0,
    },
  }
}

function buildBudgetConfig(
  overrides: Partial<BudgetConfig> = {},
): BudgetConfig {
  return {
    total_monthly: 0,
    alerts: { warn_at: 0.8, critical_at: 0.9, hard_stop_at: 1 },
    per_task_limit: 10,
    per_agent_daily_limit: 50,
    reset_day: 1,
    currency: DEFAULT_CURRENCY,
    pte_tracking_enabled: false,
    forecast_required: true,
    forecast_default_ceiling_multiplier: 1.5,
    run_hard_ceiling: 0,
    run_hard_token_ceiling: 50000000,
    session_token_ceiling: 2000000,
    forecast_static_prior_per_turn_expert: 0.1,
    forecast_static_prior_per_turn_capable: 0.03,
    forecast_static_prior_per_turn_basic: 0.005,
    forecast_static_prior_per_turn_local: 0,
    forecast_shrinkage_prior_weight: 5,
    benchmark_provider: 'measured',
    model_capability_overrides: {},
    risk_budget: {
      alerts: { critical_at: 90, warn_at: 75 },
      enabled: false,
      per_agent_daily_risk_limit: 20,
      per_task_risk_limit: 5,
      total_daily_risk_limit: 100,
    },
    call_analytics: {
      enabled: true,
      orchestration_alerts: { critical: 0.7, info: 0.3, warn: 0.5 },
      retry_alerts: { warn_rate: 0.1 },
      prompt_class_alerts: { cost_warn: null, p95_latency_warn_ms: null, min_seconds_between_alerts: 300 },
    },
    subscriptions: {},
    ...overrides,
  }
}

export const budgetHandlers = [
  http.get('/api/v1/budget/config', () =>
    HttpResponse.json(successFor<typeof getBudgetConfig>(buildBudgetConfig())),
  ),
  http.get('/api/v1/budget/records', () => {
    // `listCostRecords()` collapses the paginated envelope to a flat
    // `CostRecordListResult`, so `paginatedFor<typeof endpoint>` can't
    // represent the wire shape. Bind the body to the exported wire type
    // instead for compile-time drift detection.
    const body: CostRecordListResponseBody = {
      success: true,
      data: [],
      error: null,
      error_detail: null,
      pagination: {
        limit: 200,
        next_cursor: null,
        has_more: false,
      },
      degraded_sources: [],
      daily_summary: [],
      period_summary: {
        avg_cost: 0,
        total_cost: 0,
        total_input_tokens: 0,
        total_output_tokens: 0,
        record_count: 0,
        currency: DEFAULT_CURRENCY,
      },
      currency: DEFAULT_CURRENCY,
    }
    return HttpResponse.json(body)
  }),
  http.get('/api/v1/budget/agents/:agentId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgentSpending>({
        agent_id: String(params['agentId']),
        total_cost: 0,
        currency: DEFAULT_CURRENCY,
      } satisfies AgentSpending),
    ),
  ),
  http.get('/api/v1/budget/pareto', () =>
    HttpResponse.json(
      successFor<typeof getParetoFrontier>(buildParetoFrontier()),
    ),
  ),
  http.post('/api/v1/budget/forecast', () =>
    HttpResponse.json(successFor<typeof createForecast>(buildForecast())),
  ),
  http.get('/api/v1/budget/forecasts/:forecastId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getForecast>(
        buildForecast({ forecast_id: String(params['forecastId']) }),
      ),
    ),
  ),
  http.post('/api/v1/budget/forecasts/:forecastId/approve', ({ params }) =>
    HttpResponse.json(
      successFor<typeof approveForecast>(
        buildForecast({
          forecast_id: String(params['forecastId']),
          decision: 'approved',
          decided_at: '2026-05-20T12:30:00Z',
          decided_by: 'operator',
        }),
      ),
    ),
  ),
  http.post('/api/v1/budget/forecasts/:forecastId/reject', ({ params }) =>
    HttpResponse.json(
      successFor<typeof rejectForecast>(
        buildForecast({
          forecast_id: String(params['forecastId']),
          decision: 'rejected',
          decided_at: '2026-05-20T12:30:00Z',
          decided_by: 'operator',
        }),
      ),
    ),
  ),
  http.post('/api/v1/budget/forecasts/:forecastId/raise_ceiling', ({ params }) =>
    HttpResponse.json(
      successFor<typeof raiseCeiling>(
        buildForecast({
          forecast_id: String(params['forecastId']),
          decision: 'approved',
          ceiling_amount: 2.5,
        }),
      ),
    ),
  ),
  http.get('/api/v1/budget/call-analytics', () =>
    HttpResponse.json(
      successFor<typeof getCallAnalytics>(buildCallAnalytics()),
    ),
  ),
  http.get('/api/v1/budget/prompt-class-breakdown', () =>
    HttpResponse.json(
      successFor<typeof getPromptClassBreakdown>({
        rows: [],
      } satisfies PromptClassBreakdown),
    ),
  ),
]

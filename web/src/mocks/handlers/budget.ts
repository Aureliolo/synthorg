import { http, HttpResponse } from 'msw'
import type {
  CostRecordListResponseBody,
  getAgentSpending,
  getBudgetConfig,
} from '@/api/endpoints/budget'
import type { AgentSpending, BudgetConfig } from '@/api/types/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { successFor } from './helpers'

export function buildBudgetConfig(
  overrides: Partial<BudgetConfig> = {},
): BudgetConfig {
  return {
    total_monthly: 0,
    alerts: { warn_at: 0.8, critical_at: 0.9, hard_stop_at: 1 },
    per_task_limit: 10,
    per_agent_daily_limit: 50,
    auto_downgrade: {
      enabled: false,
      threshold: 0.8,
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
    risk_budget: {
      alerts: { critical_at: 90, warn_at: 75 },
      enabled: false,
      per_agent_daily_risk_limit: 20,
      per_task_risk_limit: 5,
      total_daily_risk_limit: 100,
    },
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
        total: 0,
        offset: 0,
        limit: 200,
        next_cursor: null,
        has_more: false,
      },
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
        agent_id: String(params.agentId),
        total_cost: 0,
        currency: DEFAULT_CURRENCY,
      } satisfies AgentSpending),
    ),
  ),
]

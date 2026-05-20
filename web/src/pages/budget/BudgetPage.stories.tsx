import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import { useBudgetStore } from '@/stores/budget'
import type {
  ForecastResponse,
  OverviewMetrics,
  TrendDataPoint,
} from '@/api/types/analytics'
import type { BudgetConfig, CostRecord } from '@/api/types/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import BudgetPage from '@/pages/BudgetPage'

const mockOverview: OverviewMetrics = {
  total_tasks: 42,
  tasks_by_status: {} as Record<string, number>,
  total_agents: 8,
  total_cost: 42.17,
  budget_remaining: 57.83,
  budget_used_percent: 42,
  cost_7d_trend: [
    { timestamp: '2026-03-20', value: 4 },
    { timestamp: '2026-03-21', value: 5 },
    { timestamp: '2026-03-22', value: 6 },
    { timestamp: '2026-03-23', value: 7 },
    { timestamp: '2026-03-24', value: 6 },
    { timestamp: '2026-03-25', value: 8 },
    { timestamp: '2026-03-26', value: 6 },
  ] as TrendDataPoint[],
  active_agents_count: 5,
  idle_agents_count: 3,
  currency: DEFAULT_CURRENCY,
}

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 100,
  alerts: { warn_at: 75, critical_at: 90, hard_stop_at: 100 },
  per_task_limit: 5,
  per_agent_daily_limit: 20,
  auto_downgrade: { enabled: true, threshold: 85, downgrade_map: [], boundary: 'task_assignment' },
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
}

const mockForecast: ForecastResponse = {
  horizon_days: 14,
  projected_total: 80,
  daily_projections: [
    { day: '2026-03-27', projected_spend: 48 },
    { day: '2026-03-28', projected_spend: 54 },
  ],
  days_until_exhausted: 20,
  confidence: 0.82,
  avg_daily_spend: 6.02,
  currency: DEFAULT_CURRENCY,
}

function makeCostRecord(fields: Partial<CostRecord> & Pick<CostRecord, 'agent_id' | 'task_id' | 'provider' | 'model' | 'input_tokens' | 'output_tokens' | 'cost' | 'timestamp' | 'call_category'>): CostRecord {
  return {
    project_id: null,
    currency: DEFAULT_CURRENCY,
    accuracy_effort_ratio: null,
    latency_ms: null,
    cache_hit: null,
    retry_count: null,
    retry_reason: null,
    finish_reason: null,
    success: null,
    claim_id: '',
    ...fields,
  }
}

const mockCostRecords: CostRecord[] = [
  makeCostRecord({ agent_id: 'a1', task_id: 't1', provider: 'prov-a', model: 'm1', input_tokens: 500, output_tokens: 200, cost: 15, timestamp: '2026-03-25T10:00:00Z', call_category: 'productive' }),
  makeCostRecord({ agent_id: 'a1', task_id: 't2', provider: 'prov-a', model: 'm1', input_tokens: 300, output_tokens: 100, cost: 8, timestamp: '2026-03-25T11:00:00Z', call_category: 'coordination' }),
  makeCostRecord({ agent_id: 'a2', task_id: 't3', provider: 'prov-b', model: 'm2', input_tokens: 800, output_tokens: 400, cost: 12, timestamp: '2026-03-25T12:00:00Z', call_category: 'productive' }),
  makeCostRecord({ agent_id: 'a3', task_id: 't4', provider: 'prov-a', model: 'm1', input_tokens: 200, output_tokens: 100, cost: 5, timestamp: '2026-03-25T13:00:00Z', call_category: 'system' }),
  makeCostRecord({ agent_id: 'a1', task_id: 't5', provider: 'prov-a', model: 'm1', input_tokens: 400, output_tokens: 0, cost: 3.5, timestamp: '2026-03-25T14:00:00Z', call_category: 'embedding' }),
  makeCostRecord({ agent_id: 'a2', task_id: 't6', provider: 'prov-b', model: 'm2', input_tokens: 100, output_tokens: 50, cost: 2.17, timestamp: '2026-03-25T15:00:00Z', call_category: null }),
]

function setStoreState(overrides: Record<string, unknown> = {}) {
  useBudgetStore.setState({
    overview: mockOverview,
    budgetConfig: mockBudgetConfig,
    forecast: mockForecast,
    costRecords: mockCostRecords,
    dailySummary: [],
    periodSummary: null,
    trends: {
      period: '30d',
      metric: 'spend',
      bucket_size: 'day',
      data_points: mockOverview.cost_7d_trend,
    },
    activities: [
      { id: '1', timestamp: '2026-03-26T10:00:00Z', agent_name: 'CFO Bot', action_type: 'budget.record_added' as const, description: 'recorded a cost', task_id: null, department: null },
      { id: '2', timestamp: '2026-03-26T09:00:00Z', agent_name: 'CFO Bot', action_type: 'budget.alert' as const, description: 'triggered a budget alert', task_id: null, department: null },
    ],
    agentNameMap: new Map([['a1', 'Alpha'], ['a2', 'Beta'], ['a3', 'Gamma']]),
    agentDeptMap: new Map([['a1', 'Engineering'], ['a2', 'Engineering'], ['a3', 'Operations']]),
    aggregationPeriod: 'daily' as const,
    loading: false,
    error: null,
    // Override store actions with no-ops to prevent live side effects in Storybook
    fetchBudgetData: async () => {},
    fetchOverview: async () => {},
    fetchTrends: async () => {},
    updateFromWsEvent: () => {},
    ...overrides,
  })
}

const meta: Meta<typeof BudgetPage> = {
  title: 'Pages/Budget',
  component: BudgetPage,
  parameters: { a11y: { test: 'error' } },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="p-6">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
}

export default meta
type Story = StoryObj<typeof BudgetPage>

export const WithData: Story = {
  decorators: [
    (Story) => {
      setStoreState()
      return <Story />
    },
  ],
}

export const AmberZone: Story = {
  decorators: [
    (Story) => {
      setStoreState({
        overview: { ...mockOverview, budget_used_percent: 82, budget_remaining: 18, total_cost: 82 },
      })
      return <Story />
    },
  ],
}

export const RedZone: Story = {
  decorators: [
    (Story) => {
      setStoreState({
        overview: { ...mockOverview, budget_used_percent: 96, budget_remaining: 4, total_cost: 96 },
      })
      return <Story />
    },
  ],
}

export const Loading: Story = {
  decorators: [
    (Story) => {
      setStoreState({ overview: null, loading: true })
      return <Story />
    },
  ],
}

export const Error: Story = {
  decorators: [
    (Story) => {
      setStoreState({ error: 'Failed to load budget data. Please try again.' })
      return <Story />
    },
  ],
}

export const Empty: Story = {
  decorators: [
    (Story) => {
      setStoreState({ costRecords: [], activities: [] })
      return <Story />
    },
  ],
}

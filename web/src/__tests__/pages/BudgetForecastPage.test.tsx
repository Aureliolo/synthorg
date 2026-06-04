import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { UseBudgetDataReturn } from '@/hooks/useBudgetData'
import type { ForecastResponse, OverviewMetrics, TrendDataPoint } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'

// -- Mock data ---------------------------------------------------------------

const mockOverview: OverviewMetrics = {
  total_tasks: 10,
  tasks_by_status: {},
  total_agents: 5,
  total_cost: 42,
  budget_remaining: 58,
  budget_used_percent: 42,
  cost_7d_trend: [
    { timestamp: '2026-03-20', value: 5 },
    { timestamp: '2026-03-21', value: 6 },
    { timestamp: '2026-03-22', value: 7 },
  ] as TrendDataPoint[],
  active_agents_count: 3,
  idle_agents_count: 2,
  currency: 'EUR',
}

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 100,
  alerts: { warn_at: 75, critical_at: 90, hard_stop_at: 100 },
  per_task_limit: 5,
  per_agent_daily_limit: 20,
  auto_downgrade: { enabled: false, threshold: 85, downgrade_map: [], boundary: 'task_assignment' },
  reset_day: 1,
  currency: 'EUR',
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
  daily_projections: [
    { day: 'Day 1', projected_spend: 3 },
    { day: 'Day 2', projected_spend: 4 },
    { day: 'Day 3', projected_spend: 5 },
  ],
  days_until_exhausted: 20,
  confidence: 0.8,
  avg_daily_spend: 3,
  currency: 'EUR',
}

const defaultHookReturn: UseBudgetDataReturn = {
  overview: mockOverview,
  budgetConfig: mockBudgetConfig,
  forecast: mockForecast,
  costRecords: [],
  trends: null,
  activities: [],
  agentNameMap: new Map(),
  agentDeptMap: new Map(),
  aggregationPeriod: 'daily',
  setAggregationPeriod: vi.fn(),
  loading: false,
  error: null,
  pollingError: null,
  isRefetching: false,
  wsConnected: true,
  wsSetupError: null,
}

// -- Hook mock ---------------------------------------------------------------

let hookReturn: UseBudgetDataReturn
const getBudgetData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useBudgetData', () => {
  const hookName = 'useBudgetData'
  return { [hookName]: () => getBudgetData() }
})

// Must import page AFTER vi.mock
import BudgetForecastPage from '@/pages/BudgetForecastPage'

function renderWithRouter() {
  return render(
    <MemoryRouter>
      <BudgetForecastPage />
    </MemoryRouter>,
  )
}

// -- Tests -------------------------------------------------------------------

beforeEach(() => {
  vi.clearAllMocks()
  hookReturn = { ...defaultHookReturn }
})

describe('BudgetForecastPage', () => {
  it('renders page heading', () => {
    renderWithRouter()
    expect(screen.getByText('Budget Forecast')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no overview', () => {
    hookReturn = { ...defaultHookReturn, overview: null, loading: true }
    renderWithRouter()
    expect(screen.getByLabelText('Loading forecast')).toBeInTheDocument()
  })

  it('does not show skeleton when loading but overview exists', () => {
    hookReturn = { ...defaultHookReturn, loading: true }
    renderWithRouter()
    expect(screen.queryByLabelText('Loading forecast')).not.toBeInTheDocument()
    expect(screen.getByText('Budget Forecast')).toBeInTheDocument()
  })

  it('renders 4 metric cards', () => {
    renderWithRouter()
    expect(screen.getByText('PROJECTED TOTAL')).toBeInTheDocument()
    expect(screen.getByText('DAYS UNTIL EXHAUSTED')).toBeInTheDocument()
    expect(screen.getByText('CONFIDENCE')).toBeInTheDocument()
    expect(screen.getByText('AVG DAILY SPEND')).toBeInTheDocument()
  })

  it('shows empty state when forecast is null', () => {
    hookReturn = { ...defaultHookReturn, forecast: null }
    renderWithRouter()
    expect(screen.getByText('No forecast data')).toBeInTheDocument()
  })

  it('renders daily projections table with cumulative values', () => {
    renderWithRouter()
    expect(screen.getByText('Daily Projections')).toBeInTheDocument()
    expect(screen.getByText('Day 1')).toBeInTheDocument()
    expect(screen.getByText('Day 2')).toBeInTheDocument()
    expect(screen.getByText('Day 3')).toBeInTheDocument()
  })

  it('renders breadcrumb navigation back to Budget', () => {
    renderWithRouter()
    const nav = screen.getByRole('navigation', { name: 'Breadcrumb' })
    expect(nav).toBeInTheDocument()
    // Scope the link query to the breadcrumb nav so we do not accidentally
    // match unrelated "Budget" links that might exist elsewhere on the
    // page.
    const budgetLink = within(nav).getByRole('link', { name: 'Budget' })
    expect(budgetLink).toHaveAttribute('href', '/budget')
    // Terminal crumb carries aria-current="page" per the Breadcrumbs
    // component contract.
    const terminal = within(nav).getByText('Forecast').closest('[aria-current="page"]')
    expect(terminal).not.toBeNull()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Something went wrong' }
    renderWithRouter()
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('shows WS disconnect warning when not connected', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false }
    renderWithRouter()
    expect(screen.getByText(/Real-time updates disconnected/)).toBeInTheDocument()
  })

  it('shows custom ws error message', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false, wsSetupError: 'WebSocket auth failed' }
    renderWithRouter()
    expect(screen.getByText('WebSocket auth failed')).toBeInTheDocument()
  })

  it('shows "--" for confidence when value is NaN', () => {
    hookReturn = {
      ...defaultHookReturn,
      forecast: { ...mockForecast, confidence: NaN },
    }
    renderWithRouter()
    // Scope to the MetricCard root by walking up from the label span
    // to the nearest ancestor with the card-shell class. MetricCard
    // wraps its content in a ``rounded-lg border ... bg-card p-card``
    // div which is the unambiguous root for the assertion.
    const labels = screen.getAllByText(/^CONFIDENCE$/i)
    const cardRoot = labels[0]?.closest('[class*="bg-card"]')
    expect(cardRoot?.textContent ?? '').toContain('--')
  })

  it('shows "--" for confidence when value is undefined', () => {
    hookReturn = {
      ...defaultHookReturn,
      // The forecast type requires `confidence: number`, but the runtime
      // path has to tolerate `undefined` from the API. We assert the
      // fallback explicitly.
      // @ts-expect-error -- intentional: cover undefined fallback path
      forecast: { ...mockForecast, confidence: undefined },
    }
    renderWithRouter()
    const labels = screen.getAllByText(/^CONFIDENCE$/i)
    const cardRoot = labels[0]?.closest('[class*="bg-card"]')
    expect(cardRoot?.textContent ?? '').toContain('--')
  })

  it('shows "N/A" for days until exhausted when null', () => {
    hookReturn = {
      ...defaultHookReturn,
      forecast: { ...mockForecast, days_until_exhausted: null },
    }
    renderWithRouter()
    const labels = screen.getAllByText(/DAYS UNTIL EXHAUSTED/i)
    const cardRoot = labels[0]?.closest('[class*="bg-card"]')
    expect(cardRoot?.textContent ?? '').toContain('N/A')
  })

  it('does not show empty state when error is present and forecast is null', () => {
    hookReturn = { ...defaultHookReturn, forecast: null, error: 'Failed to load' }
    renderWithRouter()
    expect(screen.queryByText('No forecast data')).not.toBeInTheDocument()
    expect(screen.getByText('Failed to load')).toBeInTheDocument()
  })

  it('renders projections in a semantic table', () => {
    renderWithRouter()
    const table = screen.getByRole('table')
    expect(table).toBeInTheDocument()
    expect(screen.getAllByRole('columnheader')).toHaveLength(4)
  })

  it('does not render metric cards when forecast is null', () => {
    hookReturn = { ...defaultHookReturn, forecast: null }
    renderWithRouter()
    expect(screen.queryByText('PROJECTED TOTAL')).not.toBeInTheDocument()
    expect(screen.queryByText('CONFIDENCE')).not.toBeInTheDocument()
  })
})

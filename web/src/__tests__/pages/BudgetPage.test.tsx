import { beforeEach, describe, expect, it, vi } from 'vitest'
import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { UseBudgetDataReturn } from '@/hooks/useBudgetData'
import type { ForecastResponse, OverviewMetrics, TrendDataPoint } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'

// ── Mock data ──────────────────────────────────────────────

const mockOverview: OverviewMetrics = {
  total_tasks: 10,
  tasks_by_status: {} as Record<string, number>,
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
}

const mockForecast: ForecastResponse = {
  horizon_days: 14,
  projected_total: 80,
  daily_projections: [],
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
  wsConnected: true,
  wsSetupError: null,
}

// ── Hook mock ──────────────────────────────────────────────

let hookReturn: UseBudgetDataReturn
const getBudgetData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useBudgetData', () => {
  const hookName = 'useBudgetData'
  return { [hookName]: () => getBudgetData() }
})

// Must import page AFTER vi.mock
import BudgetPage from '@/pages/BudgetPage'

function renderBudget() {
  return render(
    <MemoryRouter>
      <BudgetPage />
    </MemoryRouter>,
  )
}

// ── Tests ──────────────────────────────────────────────────

beforeEach(() => {
  vi.clearAllMocks()
  hookReturn = { ...defaultHookReturn }
})

describe('BudgetPage', () => {
  it('renders page heading', () => {
    renderBudget()
    expect(screen.getByText('Budget')).toBeInTheDocument()
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultHookReturn, overview: null, loading: true }
    renderBudget()
    expect(screen.getByLabelText('Loading budget')).toBeInTheDocument()
  })

  it('does not show skeleton when loading but data exists', () => {
    hookReturn = { ...defaultHookReturn, loading: true }
    renderBudget()
    expect(screen.queryByLabelText('Loading budget')).not.toBeInTheDocument()
    expect(screen.getByText('Budget')).toBeInTheDocument()
  })

  it('renders 4 metric cards', () => {
    renderBudget()
    expect(screen.getByText('SPEND THIS PERIOD')).toBeInTheDocument()
    expect(screen.getByText('BUDGET REMAINING')).toBeInTheDocument()
    expect(screen.getByText('AVG DAILY SPEND')).toBeInTheDocument()
    expect(screen.getByText('DAYS UNTIL EXHAUSTED')).toBeInTheDocument()
  })

  it('renders Budget Status section', () => {
    renderBudget()
    expect(screen.getByText('Budget Status')).toBeInTheDocument()
  })

  // SpendBurnChart and CostBreakdownChart are React.lazy-loaded so
  // the recharts bundle defers to first chart render; the test must
  // await the Suspense boundary to resolve before the section title
  // is in the DOM. The 10000ms timeout (vs the default 1000ms) is a
  // headroom band-aid for heavy parallel test load -- under normal
  // sequential runs the dynamic import resolves in well under 1s.
  // If these tests start consistently approaching 10s, treat that
  // as a real performance regression in the lazy-import strategy
  // rather than raising the timeout further.
  it('renders Spend Burn section', async () => {
    renderBudget()
    expect(
      await screen.findByText('Spend Burn', undefined, { timeout: 10000 }),
    ).toBeInTheDocument()
  }, 15000)

  it('renders Cost Breakdown section', async () => {
    renderBudget()
    expect(
      await screen.findByText('Cost Breakdown', undefined, { timeout: 10000 }),
    ).toBeInTheDocument()
  }, 15000)

  it('renders Cost Categories section', () => {
    renderBudget()
    expect(screen.getByText('Cost Categories')).toBeInTheDocument()
  })

  it('renders Agent Spending section', () => {
    renderBudget()
    expect(screen.getByText('Agent Spending')).toBeInTheDocument()
  })

  it('renders CFO Optimization Events section', () => {
    renderBudget()
    expect(screen.getByText('CFO Optimization Events')).toBeInTheDocument()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Something went wrong' }
    renderBudget()
    expect(screen.getByText('Something went wrong')).toBeInTheDocument()
  })

  it('shows WS disconnect warning when not connected', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false }
    renderBudget()
    expect(screen.getByText(/Real-time updates disconnected/)).toBeInTheDocument()
  })

  it('shows custom ws error message', () => {
    hookReturn = { ...defaultHookReturn, wsConnected: false, wsSetupError: 'WebSocket auth failed' }
    renderBudget()
    expect(screen.getByText('WebSocket auth failed')).toBeInTheDocument()
  })

  it('renders PeriodSelector', () => {
    renderBudget()
    expect(screen.getByRole('radiogroup', { name: 'Aggregation period' })).toBeInTheDocument()
  })
})

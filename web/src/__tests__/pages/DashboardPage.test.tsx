import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import type { UseDashboardDataReturn } from '@/hooks/useDashboardData'
import type { OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'

function makeTasksByStatus(overrides: Partial<OverviewMetrics['tasks_by_status']> = {}): OverviewMetrics['tasks_by_status'] {
  return {
    created: 0, assigned: 0, in_progress: 0, in_review: 0, completed: 0,
    blocked: 0, failed: 0, interrupted: 0, suspended: 0, cancelled: 0, rejected: 0, auth_required: 0,
    ...overrides,
  }
}

const mockOverview: OverviewMetrics = {
  total_tasks: 24,
  tasks_by_status: makeTasksByStatus({
    created: 2, assigned: 3, in_progress: 8, in_review: 2, completed: 5,
    blocked: 1, failed: 1, interrupted: 1, cancelled: 1,
  }),
  total_agents: 10,
  total_cost: 42.17,
  budget_remaining: 457.83,
  budget_used_percent: 8.43,
  budget_measurability: 'measured',
  cost_7d_trend: [
    { timestamp: '2026-03-20', value: 5 },
    { timestamp: '2026-03-21', value: 6 },
  ],
  tasks_7d_trend: [],
  agents_7d_trend: [],
  review_7d_trend: [],
  active_agents_count: 5,
  idle_agents_count: 4,
  // Deliberately distinct from tasks_by_status.failed (1): the FAILED RUNS card
  // must read task_outcomes.failed, and 6 appears nowhere else in this fixture,
  // so a wrong-source-field wiring bug fails the value assertion below.
  task_outcomes: { succeeded: 9, empty: 3, failed: 6 },
  currency: 'EUR',
}

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 500,
  alerts: { warn_at: 80, critical_at: 95, hard_stop_at: 100 },
  per_task_limit: 10,
  per_agent_daily_limit: 20,
  reset_day: 1,
  currency: 'EUR',
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
}

const defaultHookReturn: UseDashboardDataReturn = {
  overview: mockOverview,
  forecast: null,
  activities: [],
  budgetConfig: mockBudgetConfig,
  running: [],
  queue: { queued: 0, idleAgents: 0 },
  blockers: [],
  runningError: null,
  blockersError: null,
  runningLoading: false,
  blockersLoading: false,
  loading: false,
  error: null,
  isRefetching: false,
  wsConnected: true,
  wsSetupError: null,
}

let hookReturn = { ...defaultHookReturn }

const getDashboardData = vi.fn(() => hookReturn)
vi.mock('@/hooks/useDashboardData', () => {
  const hookName = 'useDashboardData'
  return { [hookName]: () => getDashboardData() }
})

// Static import: vi.mock is hoisted so the mock is applied before import
import DashboardPage from '@/pages/DashboardPage'

function renderDashboard() {
  return render(
    <MemoryRouter>
      <DashboardPage />
    </MemoryRouter>,
  )
}

describe('DashboardPage', () => {
  beforeEach(() => {
    hookReturn = { ...defaultHookReturn }
  })

  // The page renders no redundant heading of its own (AppLayout owns
  // the page title); the metric-card grid is the page's identity.
  it('renders the metric-card grid', () => {
    renderDashboard()
    expect(screen.getAllByTestId('metric-value').length).toBeGreaterThan(0)
  })

  it('renders loading skeleton when loading with no data', () => {
    hookReturn = { ...defaultHookReturn, loading: true, overview: null }
    renderDashboard()
    expect(screen.getByLabelText('Loading dashboard')).toBeInTheDocument()
  })

  it('renders 4 metric cards', () => {
    renderDashboard()
    expect(screen.getByText('TASKS')).toBeInTheDocument()
    expect(screen.getByText('ACTIVE AGENTS')).toBeInTheDocument()
    expect(screen.getByText('SPEND')).toBeInTheDocument()
    expect(screen.getByText('FAILED RUNS')).toBeInTheDocument()
  })

  it('renders metric values', () => {
    renderDashboard()
    expect(screen.getByText('24')).toBeInTheDocument() // total_tasks
    expect(screen.getByText('5')).toBeInTheDocument()  // active_agents
    // FAILED RUNS reads task_outcomes.failed (6), not tasks_by_status.failed (1).
    // Scope to the FAILED RUNS card so the value assertion can't accidentally
    // match a '6' rendered by any other card.
    const failedRunsCard = screen.getByRole('group', { name: 'FAILED RUNS' })
    expect(within(failedRunsCard).getByTestId('metric-value')).toHaveTextContent(
      '6',
    )
    // Subtext binds the succeeded + empty counts from task_outcomes.
    expect(screen.getByText('9 succeeded, 3 produced nothing')).toBeInTheDocument()
  })

  it('renders Org Pulse section', () => {
    renderDashboard()
    expect(screen.getByText('Org Pulse')).toBeInTheDocument()
  })

  it('renders Activity section', () => {
    renderDashboard()
    expect(screen.getByText('Live Activity')).toBeInTheDocument()
  })

  // BudgetBurnChart is React.lazy-loaded so the recharts bundle defers
  // to first chart render; the test must await the Suspense boundary.
  // The 5000ms timeout (vs default 1000ms) accommodates heavy parallel
  // test loads where the dynamic import takes longer to resolve.
  it('renders Budget Burn section', async () => {
    renderDashboard()
    expect(
      await screen.findByText('Budget Burn', undefined, { timeout: 5000 }),
    ).toBeInTheDocument()
  })

  it('shows error banner when error is set', () => {
    hookReturn = { ...defaultHookReturn, error: 'Connection lost' }
    renderDashboard()
    expect(screen.getByText('Connection lost')).toBeInTheDocument()
  })

  it('does not show skeleton when loading but data already exists', () => {
    hookReturn = { ...defaultHookReturn, loading: true }
    renderDashboard()
    // Should show the page, not the skeleton
    expect(screen.getByText('Org Pulse')).toBeInTheDocument()
    expect(screen.queryByLabelText('Loading dashboard')).not.toBeInTheDocument()
  })

  // WebSocket connection status is now shown globally in Sidebar/StatusBar,
  // not as an inline warning on the Dashboard page.
})

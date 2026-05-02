import { render, screen } from '@testing-library/react'
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
  cost_7d_trend: [
    { timestamp: '2026-03-20', value: 5 },
    { timestamp: '2026-03-21', value: 6 },
  ],
  active_agents_count: 5,
  idle_agents_count: 4,
  currency: 'EUR',
}

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 500,
  alerts: { warn_at: 80, critical_at: 95, hard_stop_at: 100 },
  per_task_limit: 10,
  per_agent_daily_limit: 20,
  auto_downgrade: { enabled: false, threshold: 90, downgrade_map: [], boundary: 'task_assignment' },
  reset_day: 1,
  currency: 'EUR',
}

const defaultHookReturn: UseDashboardDataReturn = {
  overview: mockOverview,
  forecast: null,
  departmentHealths: [],
  activities: [],
  budgetConfig: mockBudgetConfig,
  orgHealthPercent: null,
  loading: false,
  error: null,
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

  it('renders page heading', () => {
    renderDashboard()
    expect(screen.getByText('Overview')).toBeInTheDocument()
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
    expect(screen.getByText('IN REVIEW')).toBeInTheDocument()
  })

  it('renders metric values', () => {
    renderDashboard()
    expect(screen.getByText('24')).toBeInTheDocument() // total_tasks
    expect(screen.getByText('5')).toBeInTheDocument()  // active_agents
  })

  it('renders Org Health section', () => {
    renderDashboard()
    expect(screen.getByText('Org Health')).toBeInTheDocument()
  })

  it('renders Activity section', () => {
    renderDashboard()
    expect(screen.getByText('Activity')).toBeInTheDocument()
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
    expect(screen.getByText('Overview')).toBeInTheDocument()
    expect(screen.queryByLabelText('Loading dashboard')).not.toBeInTheDocument()
  })

  // WebSocket connection status is now shown globally in Sidebar/StatusBar,
  // not as an inline warning on the Dashboard page.
})

import { render, screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { useAnalyticsStore } from '@/stores/analytics'
import { StatusBar } from '@/components/layout/StatusBar'
import { formatCurrency } from '@/utils/format'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { apiError } from '@/mocks/handlers'
import { server } from '@/test-setup'
import type { OverviewMetrics } from '@/api/types/analytics'

function makeOverview(overrides: Partial<OverviewMetrics> = {}): OverviewMetrics {
  return {
    total_tasks: 0,
    tasks_by_status: {
      created: 0,
      assigned: 0,
      in_progress: 0,
      in_review: 0,
      completed: 0,
      blocked: 0,
      failed: 0,
      interrupted: 0,
      suspended: 0,
      cancelled: 0,
    },
    total_agents: 0,
    total_cost: 0,
    budget_remaining: 0,
    budget_used_percent: 0,
    cost_7d_trend: [],
    active_agents_count: 0,
    idle_agents_count: 0,
    currency: DEFAULT_CURRENCY,
    ...overrides,
  }
}

// The StatusBar schedules a usePolling tick on mount -- stub it so the
// component doesn't run its real poll interval in tests.
vi.mock('@/hooks/usePolling', () => ({
  usePolling: vi.fn().mockReturnValue({
    active: false,
    error: null,
    start: vi.fn(),
    stop: vi.fn(),
  }),
}))

function resetStore() {
  useAnalyticsStore.setState({
    overview: null,
    forecast: null,
    departmentHealths: [],
    activities: [],
    budgetConfig: null,
    orgHealthPercent: null,
    loading: false,
    error: null,
  })
}

describe('StatusBar', () => {
  beforeEach(() => {
    resetStore()
    // The component fires fetchDashboardData on mount; block it with a
    // 5xx envelope so the store never populates from the MSW default.
    // Tests that want populated state set the store directly via
    // useAnalyticsStore.setState before rendering.
    server.use(
      http.get('/api/v1/analytics/overview', () =>
        HttpResponse.json(apiError('blocked for StatusBar placeholder test')),
      ),
      http.get('/api/v1/analytics/forecast', () =>
        HttpResponse.json(apiError('blocked for StatusBar placeholder test')),
      ),
      http.get('/api/v1/budget/config', () =>
        HttpResponse.json(apiError('blocked for StatusBar placeholder test')),
      ),
      http.get('/api/v1/activities', () =>
        HttpResponse.json(apiError('blocked for StatusBar placeholder test')),
      ),
      http.get('/api/v1/departments', () =>
        HttpResponse.json(apiError('blocked for StatusBar placeholder test')),
      ),
      http.get('/api/v1/readyz', () =>
        HttpResponse.json(apiError('blocked for StatusBar placeholder test')),
      ),
    )
  })

  it('does not duplicate the SynthOrg brand text (sidebar already shows it)', () => {
    render(<StatusBar />)
    expect(screen.queryByText('SynthOrg')).not.toBeInTheDocument()
  })

  it('shows placeholder values when no data loaded', () => {
    render(<StatusBar />)
    const dashes = screen.getAllByText('--')
    expect(dashes.length).toBeGreaterThanOrEqual(3)
  })

  it('shows live values from analytics store', () => {
    useAnalyticsStore.setState({
      overview: makeOverview({
        total_tasks: 42,
        total_agents: 12,
        total_cost: 85.5,
        budget_remaining: 414.5,
        budget_used_percent: 17.1,
        active_agents_count: 8,
        idle_agents_count: 3,
      }),
    })

    render(<StatusBar />)
    expect(screen.getByText('12 agents')).toBeInTheDocument()
    expect(screen.getByText('8 active')).toBeInTheDocument()
    expect(screen.getByText('3 idle')).toBeInTheDocument()
    expect(screen.getByText('42 tasks')).toBeInTheDocument()
  })

  it('renders unknown system status before first poll', () => {
    render(<StatusBar />)
    expect(screen.getByText('checking...')).toBeInTheDocument()
  })

  it('shows cost placeholder when no data loaded', () => {
    render(<StatusBar />)
    const spendLabel = screen.getByText('spend')
    const spendChip = spendLabel.parentElement
    expect(spendChip).toHaveTextContent(/spend\s*--\s*today/)
  })

  it('shows budget percentage when data loaded', () => {
    useAnalyticsStore.setState({
      overview: makeOverview({
        total_tasks: 10,
        total_agents: 5,
        total_cost: 50,
        budget_remaining: 450,
        budget_used_percent: 10,
        active_agents_count: 3,
        idle_agents_count: 2,
      }),
    })
    render(<StatusBar />)
    expect(screen.getByText('10%')).toBeInTheDocument()
  })

  it('shows in-review count when non-zero', () => {
    useAnalyticsStore.setState({
      overview: makeOverview({
        total_tasks: 10,
        tasks_by_status: {
          created: 0,
          assigned: 0,
          in_progress: 0,
          in_review: 3,
          completed: 0,
          blocked: 0,
          failed: 0,
          interrupted: 0,
          suspended: 0,
          cancelled: 0,
        },
        total_agents: 5,
        total_cost: 50,
        budget_remaining: 450,
        budget_used_percent: 10,
        active_agents_count: 3,
        idle_agents_count: 2,
      }),
    })
    render(<StatusBar />)
    expect(screen.getByText('3 in review')).toBeInTheDocument()
  })

  it('shows formatted currency for cost display (default currency)', () => {
    const overview = makeOverview({ total_cost: 1234.56 })
    useAnalyticsStore.setState({ overview })
    render(<StatusBar />)
    expect(
      screen.getByText(formatCurrency(1234.56, overview.currency)),
    ).toBeInTheDocument()
  })

  it('shows formatted currency for non-default currency', () => {
    // lint-allow: regional-defaults
    const overview = makeOverview({ total_cost: 99.5, currency: 'GBP' })
    useAnalyticsStore.setState({ overview })
    render(<StatusBar />)
    expect(
      screen.getByText(formatCurrency(99.5, overview.currency)),
    ).toBeInTheDocument()
  })

  it('renders the theme toggle', () => {
    render(<StatusBar />)
    expect(
      screen.getByRole('button', { name: 'Theme preferences' }),
    ).toBeInTheDocument()
  })
})

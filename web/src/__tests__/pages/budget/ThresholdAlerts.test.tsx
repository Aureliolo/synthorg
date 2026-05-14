import { render, screen } from '@testing-library/react'
import { ThresholdAlerts } from '@/pages/budget/ThresholdAlerts'
import type { OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import type { ThresholdZone } from '@/utils/budget'

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 500,
  alerts: { warn_at: 75, critical_at: 90, hard_stop_at: 100 },
  per_task_limit: 10,
  per_agent_daily_limit: 20,
  auto_downgrade: { enabled: false, threshold: 90, downgrade_map: [], boundary: 'task_assignment' },
  reset_day: 1,
  currency: 'EUR',
  pte_tracking_enabled: false,
}

const mockOverview: OverviewMetrics = {
  total_tasks: 10,
  tasks_by_status: {
    created: 1, assigned: 1, in_progress: 2, in_review: 1, completed: 3,
    blocked: 0, failed: 1, interrupted: 0, suspended: 0, cancelled: 1, rejected: 0, auth_required: 0,
  },
  total_agents: 5,
  total_cost: 400,
  budget_remaining: 100,
  budget_used_percent: 80,
  cost_7d_trend: [],
  active_agents_count: 3,
  idle_agents_count: 2,
  currency: 'EUR',
}

describe('ThresholdAlerts', () => {
  it('returns nothing for normal zone', () => {
    const { container } = render(
      <ThresholdAlerts zone="normal" budgetConfig={mockBudgetConfig} overview={mockOverview} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('returns nothing when budgetConfig is null', () => {
    const { container } = render(
      <ThresholdAlerts zone="amber" budgetConfig={null} overview={mockOverview} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('returns nothing when overview is null', () => {
    const { container } = render(
      <ThresholdAlerts zone="amber" budgetConfig={mockBudgetConfig} overview={null} />,
    )
    expect(container.firstChild).toBeNull()
  })

  it('renders warning text for amber zone', () => {
    render(
      <ThresholdAlerts zone="amber" budgetConfig={mockBudgetConfig} overview={mockOverview} />,
    )
    expect(
      screen.getByText(/Budget usage at 80%: warning threshold \(75%\) reached/),
    ).toBeInTheDocument()
  })

  it('renders danger text for red zone', () => {
    const overview = { ...mockOverview, budget_used_percent: 92 }
    render(
      <ThresholdAlerts zone="red" budgetConfig={mockBudgetConfig} overview={overview} />,
    )
    expect(
      screen.getByText(/Budget usage at 92%: critical threshold \(90%\) reached/),
    ).toBeInTheDocument()
  })

  it('renders hard stop text for critical zone', () => {
    const overview = { ...mockOverview, budget_used_percent: 100 }
    render(
      <ThresholdAlerts zone="critical" budgetConfig={mockBudgetConfig} overview={overview} />,
    )
    expect(
      screen.getByText(/Budget hard stop at 100% reached\. Spending halted\./),
    ).toBeInTheDocument()
  })

  it('renders pulsing icon for critical zone', () => {
    const overview = { ...mockOverview, budget_used_percent: 100 }
    const { container } = render(
      <ThresholdAlerts zone="critical" budgetConfig={mockBudgetConfig} overview={overview} />,
    )
    const svg = container.querySelector('svg')
    expect(svg).toHaveClass('animate-pulse')
  })

  it('does not render pulsing icon for amber zone', () => {
    const { container } = render(
      <ThresholdAlerts zone="amber" budgetConfig={mockBudgetConfig} overview={mockOverview} />,
    )
    const svg = container.querySelector('svg')
    expect(svg).not.toHaveClass('animate-pulse')
  })

  it('does not render pulsing icon for red zone', () => {
    const overview = { ...mockOverview, budget_used_percent: 92 }
    const { container } = render(
      <ThresholdAlerts zone="red" budgetConfig={mockBudgetConfig} overview={overview} />,
    )
    const svg = container.querySelector('svg')
    expect(svg).not.toHaveClass('animate-pulse')
  })

  it('has role="alert" for screen readers', () => {
    render(
      <ThresholdAlerts zone="amber" budgetConfig={mockBudgetConfig} overview={mockOverview} />,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
  })

  it('displays decimal percentage without rounding across zone boundaries', () => {
    const overview = { ...mockOverview, budget_used_percent: 89.6 }
    render(
      <ThresholdAlerts zone="amber" budgetConfig={mockBudgetConfig} overview={overview} />,
    )
    expect(
      screen.getByText(/Budget usage at 89\.6%: warning threshold \(75%\) reached/),
    ).toBeInTheDocument()
  })

  it.each<ThresholdZone>(['amber', 'red', 'critical'])(
    'renders an alert for %s zone',
    (zone) => {
      const overview = { ...mockOverview, budget_used_percent: zone === 'amber' ? 80 : zone === 'red' ? 92 : 100 }
      render(
        <ThresholdAlerts zone={zone} budgetConfig={mockBudgetConfig} overview={overview} />,
      )
      expect(screen.getByRole('alert')).toBeInTheDocument()
    },
  )
})

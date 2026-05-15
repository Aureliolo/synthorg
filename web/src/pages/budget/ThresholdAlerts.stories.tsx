import type { Meta, StoryObj } from '@storybook/react'
import { ThresholdAlerts } from './ThresholdAlerts'
import type { OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const baseBudgetConfig: BudgetConfig = {
  total_monthly: 500,
  alerts: { warn_at: 75, critical_at: 90, hard_stop_at: 100 },
  per_task_limit: 10,
  per_agent_daily_limit: 20,
  auto_downgrade: { enabled: false, threshold: 90, downgrade_map: [], boundary: 'task_assignment' },
  reset_day: 1,
  currency: DEFAULT_CURRENCY,
  pte_tracking_enabled: false,
  risk_budget: {
    alerts: { critical_at: 90, warn_at: 75 },
    enabled: false,
    per_agent_daily_risk_limit: 20,
    per_task_risk_limit: 5,
    total_daily_risk_limit: 100,
  },
}

function makeOverview(usedPercent: number): OverviewMetrics {
  return {
    total_tasks: 10,
    tasks_by_status: {
      created: 1, assigned: 1, in_progress: 2, in_review: 1, completed: 3,
      blocked: 0, failed: 1, interrupted: 0, suspended: 0, cancelled: 1, rejected: 0, auth_required: 0,
    },
    total_agents: 5,
    total_cost: usedPercent * 5,
    budget_remaining: 500 - usedPercent * 5,
    budget_used_percent: usedPercent,
    cost_7d_trend: [],
    active_agents_count: 3,
    idle_agents_count: 2,
    currency: DEFAULT_CURRENCY,
  }
}

const meta = {
  title: 'Budget/ThresholdAlerts',
  component: ThresholdAlerts,
  tags: ['autodocs'],
  parameters: { a11y: { test: 'error' } },
  decorators: [
    (Story) => (
      <div className="max-w-2xl">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof ThresholdAlerts>

export default meta
type Story = StoryObj<typeof meta>

export const Normal: Story = {
  args: {
    zone: 'normal',
    budgetConfig: baseBudgetConfig,
    overview: makeOverview(50),
  },
}

export const Amber: Story = {
  args: {
    zone: 'amber',
    budgetConfig: baseBudgetConfig,
    overview: makeOverview(82),
  },
}

export const Red: Story = {
  args: {
    zone: 'red',
    budgetConfig: baseBudgetConfig,
    overview: makeOverview(96),
  },
}

export const Critical: Story = {
  args: {
    zone: 'critical',
    budgetConfig: baseBudgetConfig,
    overview: makeOverview(100),
  },
}

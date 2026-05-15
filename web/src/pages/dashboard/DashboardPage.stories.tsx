import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import { useAnalyticsStore } from '@/stores/analytics'
import DashboardPage from '../DashboardPage'
import type { ActivityItem, DepartmentHealth, ForecastResponse, OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const mockOverview: OverviewMetrics = {
  total_tasks: 24,
  tasks_by_status: {
    created: 2, assigned: 3, in_progress: 8, in_review: 2, completed: 5,
    blocked: 1, failed: 1, interrupted: 1, suspended: 0, cancelled: 1, rejected: 0, auth_required: 0,
  },
  total_agents: 10,
  total_cost: 42.17,
  budget_remaining: 457.83,
  budget_used_percent: 8.43,
  cost_7d_trend: [
    { timestamp: '2026-03-20', value: 5 },
    { timestamp: '2026-03-21', value: 6.2 },
    { timestamp: '2026-03-22', value: 7.1 },
    { timestamp: '2026-03-23', value: 5.5 },
    { timestamp: '2026-03-24', value: 8.3 },
    { timestamp: '2026-03-25', value: 6.9 },
    { timestamp: '2026-03-26', value: 5.17 },
  ],
  active_agents_count: 5,
  idle_agents_count: 4,
  currency: DEFAULT_CURRENCY,
}

const mockBudgetConfig: BudgetConfig = {
  total_monthly: 500,
  alerts: { warn_at: 80, critical_at: 95, hard_stop_at: 100 },
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

const mockForecast: ForecastResponse = {
  horizon_days: 7,
  projected_total: 65,
  daily_projections: [
    { day: '2026-03-27', projected_spend: 6.5 },
    { day: '2026-03-28', projected_spend: 7.0 },
    { day: '2026-03-29', projected_spend: 6.8 },
  ],
  days_until_exhausted: null,
  confidence: 0.85,
  avg_daily_spend: 6.3,
  currency: DEFAULT_CURRENCY,
}

const mockDepartments: DepartmentHealth[] = [
  { department_name: 'engineering', agent_count: 4, active_agent_count: 3, currency: DEFAULT_CURRENCY, avg_performance_score: 8.0, department_cost_7d: 18.5, cost_trend: [], collaboration_score: 7.0, utilization_percent: 92 },
  { department_name: 'design', agent_count: 2, active_agent_count: 1, currency: DEFAULT_CURRENCY, avg_performance_score: 7.5, department_cost_7d: 8.2, cost_trend: [], collaboration_score: 6.5, utilization_percent: 78 },
  { department_name: 'product', agent_count: 2, active_agent_count: 2, currency: DEFAULT_CURRENCY, avg_performance_score: 8.2, department_cost_7d: 7.1, cost_trend: [], collaboration_score: 7.5, utilization_percent: 85 },
  { department_name: 'operations', agent_count: 1, active_agent_count: 1, currency: DEFAULT_CURRENCY, avg_performance_score: 6.0, department_cost_7d: 4.3, cost_trend: [], collaboration_score: null, utilization_percent: 60 },
]

const mockActivities: ActivityItem[] = [
  { id: '1', timestamp: '2026-03-26T12:00:00.000Z', agent_name: 'agent-cto', action_type: 'task.created', description: 'Created auth module task', task_id: 'task-42', department: 'engineering' },
  { id: '2', timestamp: '2026-03-26T11:59:00.000Z', agent_name: 'agent-designer', action_type: 'task.status_changed', description: 'Completed wireframe review', task_id: 'task-38', department: 'design' },
  { id: '3', timestamp: '2026-03-26T11:55:00.000Z', agent_name: 'agent-devops', action_type: 'agent.status_changed', description: 'Changed status to idle', task_id: null, department: 'operations' },
  { id: '4', timestamp: '2026-03-26T11:50:00.000Z', agent_name: 'agent-qa', action_type: 'approval.submitted', description: 'Requested deployment approval', task_id: 'task-40', department: 'quality_assurance' },
  { id: '5', timestamp: '2026-03-26T11:45:00.000Z', agent_name: 'agent-eng-2', action_type: 'budget.record_added', description: 'Recorded a cost', task_id: 'task-35', department: 'engineering' },
]

function setStoreState(overrides: Partial<ReturnType<typeof useAnalyticsStore.getState>> = {}) {
  useAnalyticsStore.setState({
    overview: mockOverview,
    forecast: mockForecast,
    departmentHealths: mockDepartments,
    activities: mockActivities,
    budgetConfig: mockBudgetConfig,
    orgHealthPercent: 79,
    loading: false,
    error: null,
    ...overrides,
  })
}

const meta = {
  title: 'Pages/Dashboard',
  component: DashboardPage,
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="p-card">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof DashboardPage>

export default meta
type Story = StoryObj<typeof meta>

export const WithData: Story = {
  decorators: [
    (Story) => {
      setStoreState()
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
      setStoreState({ error: 'Failed to connect to backend API' })
      return <Story />
    },
  ],
}

export const EmptyOrg: Story = {
  decorators: [
    (Story) => {
      setStoreState({
        overview: null,
        departmentHealths: [],
        activities: [],
        orgHealthPercent: null,
      })
      return <Story />
    },
  ],
}

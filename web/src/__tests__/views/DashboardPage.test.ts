import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), go: vi.fn() }),
  useRoute: () => ({ params: {} }),
  RouterLink: { template: '<a><slot /></a>' },
  createRouter: () => ({
    push: vi.fn(),
    replace: vi.fn(),
    go: vi.fn(),
    beforeEach: vi.fn(),
    currentRoute: { value: { path: '/' } },
  }),
  createWebHistory: vi.fn(),
}))

vi.mock('primevue/usetoast', () => ({
  useToast: () => ({ add: vi.fn() }),
}))

vi.mock('@/components/layout/AppShell.vue', () => ({
  default: { template: '<div><slot /></div>' },
}))

vi.mock('@/components/common/PageHeader.vue', () => ({
  default: {
    props: ['title', 'subtitle'],
    template: '<div><h1>{{ title }}</h1><p>{{ subtitle }}</p></div>',
  },
}))

vi.mock('@/components/common/LoadingSkeleton.vue', () => ({
  default: {
    props: ['lines'],
    template: '<div data-testid="loading-skeleton">Loading...</div>',
  },
}))

vi.mock('@/components/dashboard/MetricCard.vue', () => ({
  default: {
    props: ['title', 'value', 'icon', 'color'],
    template: '<div data-testid="metric-card"><span>{{ title }}</span><span>{{ value }}</span></div>',
  },
}))

vi.mock('@/components/dashboard/ActiveTasksSummary.vue', () => ({
  default: {
    props: ['tasks'],
    template: '<div data-testid="active-tasks">Active Tasks</div>',
  },
}))

vi.mock('@/components/dashboard/SpendingSummary.vue', () => ({
  default: {
    props: ['records', 'totalCost'],
    template: '<div data-testid="spending-summary">Spending Summary</div>',
  },
}))

vi.mock('@/components/dashboard/RecentApprovals.vue', () => ({
  default: {
    props: ['approvals'],
    template: '<div data-testid="recent-approvals">Recent Approvals</div>',
  },
}))

vi.mock('@/components/dashboard/SystemStatus.vue', () => ({
  default: {
    props: ['health', 'wsConnected'],
    template: '<div data-testid="system-status">System Status</div>',
  },
}))

vi.mock('@/api/endpoints/health', () => ({
  getHealth: vi.fn().mockResolvedValue({
    status: 'ok',
    version: '0.1.0',
    persistence: true,
    message_bus: true,
    uptime_seconds: 3600,
  }),
}))

vi.mock('@/api/endpoints/analytics', () => ({
  getOverviewMetrics: vi.fn().mockResolvedValue({
    total_tasks: 10,
    total_agents: 5,
    total_cost_usd: 42.5,
  }),
}))

vi.mock('@/api/endpoints/tasks', () => ({
  listTasks: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  createTask: vi.fn(),
  updateTask: vi.fn(),
  transitionTask: vi.fn(),
  cancelTask: vi.fn(),
  deleteTask: vi.fn(),
}))

vi.mock('@/api/endpoints/budget', () => ({
  getBudgetConfig: vi.fn().mockResolvedValue({
    total_budget_usd: 1000,
    daily_limit_usd: 100,
    agent_limit_usd: 50,
  }),
  listCostRecords: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getAgentSpending: vi.fn(),
}))

vi.mock('@/api/endpoints/approvals', () => ({
  listApprovals: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getApproval: vi.fn(),
  createApproval: vi.fn(),
  approveApproval: vi.fn(),
  rejectApproval: vi.fn(),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import DashboardPage from '@/views/DashboardPage.vue'

describe('DashboardPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(DashboardPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Dashboard" heading', () => {
    const wrapper = mount(DashboardPage)
    expect(wrapper.find('h1').text()).toBe('Dashboard')
  })

  it('shows loading skeleton initially', () => {
    const wrapper = mount(DashboardPage)
    expect(wrapper.find('[data-testid="loading-skeleton"]').exists()).toBe(true)
  })

  it('renders metric cards after loading', async () => {
    const wrapper = mount(DashboardPage)
    await flushPromises()
    const metricCards = wrapper.findAll('[data-testid="metric-card"]')
    expect(metricCards.length).toBe(4)
  })

  it('renders dashboard widgets after loading', async () => {
    const wrapper = mount(DashboardPage)
    await flushPromises()
    expect(wrapper.find('[data-testid="active-tasks"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="spending-summary"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="system-status"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="recent-approvals"]').exists()).toBe(true)
  })

  it('fetches data from all stores on mount', async () => {
    const { getHealth } = await import('@/api/endpoints/health')
    const { getOverviewMetrics } = await import('@/api/endpoints/analytics')
    const { listTasks } = await import('@/api/endpoints/tasks')
    mount(DashboardPage)
    await flushPromises()
    expect(getHealth).toHaveBeenCalled()
    expect(getOverviewMetrics).toHaveBeenCalled()
    expect(listTasks).toHaveBeenCalled()
  })
})

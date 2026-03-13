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

vi.mock('@/components/common/ErrorBoundary.vue', () => ({
  default: {
    props: ['error'],
    template: '<div><slot /></div>',
  },
}))

vi.mock('@/components/budget/BudgetConfigDisplay.vue', () => ({
  default: {
    props: ['config'],
    template: '<div data-testid="budget-config">Budget Config</div>',
  },
}))

vi.mock('@/components/budget/SpendingChart.vue', () => ({
  default: {
    props: ['records'],
    template: '<div data-testid="spending-chart">Spending Chart</div>',
  },
}))

vi.mock('@/components/budget/AgentSpendingTable.vue', () => ({
  default: {
    props: ['records'],
    template: '<div data-testid="agent-spending-table">Agent Spending</div>',
  },
}))

vi.mock('@/api/endpoints/budget', () => ({
  getBudgetConfig: vi.fn().mockResolvedValue({
    total_monthly: 1000,
    per_task_limit: 50,
    per_agent_daily_limit: 100,
    alerts: { thresholds: [], channels: [] },
    auto_downgrade: { enabled: false },
    reset_day: 1,
  }),
  listCostRecords: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getAgentSpending: vi.fn(),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import BudgetPanelPage from '@/views/BudgetPanelPage.vue'

describe('BudgetPanelPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(BudgetPanelPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Budget" heading', () => {
    const wrapper = mount(BudgetPanelPage)
    expect(wrapper.find('h1').text()).toBe('Budget')
  })

  it('fetches budget config and records on mount', async () => {
    const { getBudgetConfig, listCostRecords } = await import('@/api/endpoints/budget')
    mount(BudgetPanelPage)
    await flushPromises()
    expect(getBudgetConfig).toHaveBeenCalled()
    expect(listCostRecords).toHaveBeenCalled()
  })

  it('renders budget content after loading', async () => {
    const wrapper = mount(BudgetPanelPage)
    await flushPromises()
    expect(wrapper.text()).toContain('Daily Spending')
    expect(wrapper.text()).toContain('Agent Spending')
  })
})

import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn(), go: vi.fn() }),
  useRoute: () => ({ params: {} }),
  RouterLink: { template: '<a><slot /></a>' },
}))

vi.mock('@vue-flow/core', () => ({
  VueFlow: {
    props: ['nodes', 'edges', 'fitViewOnInit'],
    template: '<div data-testid="vue-flow"><slot /></div>',
  },
}))

vi.mock('@vue-flow/controls', () => ({
  Controls: { template: '<div data-testid="controls">Controls</div>' },
}))

vi.mock('@vue-flow/minimap', () => ({
  MiniMap: { template: '<div data-testid="minimap">MiniMap</div>' },
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

vi.mock('@/components/org-chart/OrgNode.vue', () => ({
  default: {
    props: ['data'],
    template: '<div data-testid="org-node">{{ data.label }}</div>',
  },
}))

vi.mock('@/api/endpoints/company', () => ({
  getCompanyConfig: vi.fn(),
  listDepartments: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getDepartment: vi.fn(),
}))

vi.mock('@/api/endpoints/agents', () => ({
  listAgents: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getAgent: vi.fn(),
  getAutonomy: vi.fn(),
  setAutonomy: vi.fn(),
}))

import OrgChartPage from '@/views/OrgChartPage.vue'

describe('OrgChartPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(OrgChartPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Organization Chart" heading', () => {
    const wrapper = mount(OrgChartPage)
    expect(wrapper.find('h1').text()).toBe('Organization Chart')
  })

  it('fetches departments and agents on mount', async () => {
    const { listDepartments } = await import('@/api/endpoints/company')
    const { listAgents } = await import('@/api/endpoints/agents')
    mount(OrgChartPage)
    await flushPromises()
    expect(listDepartments).toHaveBeenCalled()
    expect(listAgents).toHaveBeenCalled()
  })

  it('renders VueFlow after loading', async () => {
    const wrapper = mount(OrgChartPage)
    await flushPromises()
    expect(wrapper.find('[data-testid="vue-flow"]').exists()).toBe(true)
  })

  it('renders controls and minimap', async () => {
    const wrapper = mount(OrgChartPage)
    await flushPromises()
    expect(wrapper.find('[data-testid="controls"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="minimap"]').exists()).toBe(true)
  })
})

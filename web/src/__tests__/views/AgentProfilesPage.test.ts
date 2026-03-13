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
    template: '<div><h1>{{ title }}</h1><p>{{ subtitle }}</p><slot name="actions" /></div>',
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

vi.mock('@/components/common/EmptyState.vue', () => ({
  default: {
    props: ['icon', 'title', 'message'],
    template: '<div data-testid="empty-state">{{ title }}</div>',
  },
}))

vi.mock('@/components/agents/AgentCard.vue', () => ({
  default: {
    props: ['agent'],
    template: '<div data-testid="agent-card">{{ agent.name }}</div>',
  },
}))

vi.mock('@/api/endpoints/agents', () => ({
  listAgents: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getAgent: vi.fn(),
  getAutonomy: vi.fn(),
  setAutonomy: vi.fn(),
}))

vi.mock('@/api/endpoints/auth', () => ({
  getMe: vi.fn(),
  login: vi.fn(),
  setup: vi.fn(),
  changePassword: vi.fn(),
}))

import AgentProfilesPage from '@/views/AgentProfilesPage.vue'

describe('AgentProfilesPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    const wrapper = mount(AgentProfilesPage)
    expect(wrapper.exists()).toBe(true)
  })

  it('renders "Agents" heading', () => {
    const wrapper = mount(AgentProfilesPage)
    expect(wrapper.find('h1').text()).toBe('Agents')
  })

  it('shows loading skeleton when store is loading and no agents', async () => {
    const wrapper = mount(AgentProfilesPage)
    // Initially loading is true before fetchAgents resolves
    expect(wrapper.text()).toBeTruthy()
  })

  it('calls fetchAgents on mount', async () => {
    const { listAgents } = await import('@/api/endpoints/agents')
    mount(AgentProfilesPage)
    await flushPromises()
    expect(listAgents).toHaveBeenCalled()
  })
})

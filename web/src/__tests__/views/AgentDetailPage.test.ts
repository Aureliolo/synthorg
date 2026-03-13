import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { setActivePinia, createPinia } from 'pinia'
import type { AgentConfig } from '@/api/types'

const mockRouterPush = vi.fn()

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mockRouterPush, go: vi.fn() }),
  useRoute: () => ({ params: { name: 'test-agent' } }),
  RouterLink: { template: '<a><slot /></a>' },
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
    template: '<div><div v-if="error" data-testid="error">{{ error }}</div><slot v-else /></div>',
  },
}))

vi.mock('@/components/agents/AgentMetrics.vue', () => ({
  default: {
    props: ['agent'],
    template: '<div data-testid="agent-metrics">Agent Metrics</div>',
  },
}))

vi.mock('primevue/button', () => ({
  default: {
    props: ['label', 'icon', 'text', 'size'],
    template: '<button>{{ label }}</button>',
  },
}))

const mockGetAgent = vi.fn()

vi.mock('@/api/endpoints/agents', () => ({
  listAgents: vi.fn().mockResolvedValue({ data: [], total: 0 }),
  getAgent: (...args: unknown[]) => mockGetAgent(...args),
  getAutonomy: vi.fn(),
  setAutonomy: vi.fn(),
}))

import AgentDetailPage from '@/views/AgentDetailPage.vue'

const mockAgent: AgentConfig = {
  id: 'test-uuid-001',
  name: 'test-agent',
  role: 'Developer',
  department: 'engineering',
  level: 'senior',
  status: 'active',
  model: {
    provider: 'test-provider',
    model_id: 'test-model',
    temperature: 0.7,
    max_tokens: 4096,
    fallback_model: null,
  },
  personality: {
    traits: [],
    communication_style: 'neutral',
    risk_tolerance: 'medium',
    creativity: 'high',
    description: '',
    openness: 0.5,
    conscientiousness: 0.5,
    extraversion: 0.5,
    agreeableness: 0.5,
    stress_response: 0.5,
    decision_making: 'analytical',
    collaboration: 'team',
    verbosity: 'balanced',
    conflict_approach: 'collaborate',
  },
  skills: { primary: ['python'], secondary: [] },
  memory: { type: 'session', retention_days: null },
  tools: { access_level: 'standard', allowed: ['file_read'], denied: [] },
  autonomy_level: null,
  hiring_date: '2026-01-01',
}

describe('AgentDetailPage', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.clearAllMocks()
  })

  it('mounts without error', () => {
    mockGetAgent.mockResolvedValue(mockAgent)
    const wrapper = mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows loading skeleton initially', () => {
    mockGetAgent.mockImplementation(() => new Promise(() => {}))
    const wrapper = mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    expect(wrapper.find('[data-testid="loading-skeleton"]').exists()).toBe(true)
  })

  it('shows agent name when fetchAgent returns data', async () => {
    mockGetAgent.mockResolvedValue(mockAgent)
    const wrapper = mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    await flushPromises()
    expect(wrapper.find('h1').text()).toBe('test-agent')
  })

  it('shows agent role as subtitle', async () => {
    mockGetAgent.mockResolvedValue(mockAgent)
    const wrapper = mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Developer')
  })

  it('renders AgentMetrics component when agent is loaded', async () => {
    mockGetAgent.mockResolvedValue(mockAgent)
    const wrapper = mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="agent-metrics"]').exists()).toBe(true)
  })

  it('renders back button', () => {
    mockGetAgent.mockResolvedValue(mockAgent)
    const wrapper = mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    const backBtn = wrapper.find('button')
    expect(backBtn.exists()).toBe(true)
    expect(backBtn.text()).toContain('Back to Agents')
  })

  it('calls fetchAgent with the agent name prop', async () => {
    mockGetAgent.mockResolvedValue(mockAgent)
    mount(AgentDetailPage, {
      props: { name: 'test-agent' },
    })
    await flushPromises()
    expect(mockGetAgent).toHaveBeenCalledWith('test-agent')
  })
})

import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentCard from '@/components/agents/AgentCard.vue'
import type { AgentConfig } from '@/api/types'

const mockAgent: AgentConfig = {
  id: 'agent-1',
  name: 'test-agent',
  role: 'Developer',
  department: 'engineering',
  level: 'senior',
  status: 'active',
  model: { provider: 'test-provider', model_id: 'test-model', temperature: 0.7, max_tokens: 4096, fallback_model: null },
  personality: {
    risk_tolerance: 'medium',
    creativity: 'high',
    decision_making: 'analytical',
    collaboration: 'team',
    traits: [],
    communication_style: 'professional',
    description: '',
    openness: 0.7,
    conscientiousness: 0.8,
    extraversion: 0.5,
    agreeableness: 0.6,
    stress_response: 0.5,
    verbosity: 'balanced',
    conflict_approach: 'collaborate',
  },
  skills: { primary: ['typescript'], secondary: [] },
  memory: { type: 'session', retention_days: null },
  tools: { access_level: 'standard', allowed: ['file_read'], denied: [] },
  autonomy_level: null,
  hiring_date: '2026-01-01',
}

describe('AgentCard', () => {
  it('renders agent name', () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('test-agent')
  })

  it('renders agent role', () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Developer')
  })

  it('renders formatted department', () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Engineering')
  })

  it('renders formatted level', () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Senior')
  })

  it('renders model id', () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('test-model')
  })

  it('emits click event on click', async () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    await wrapper.trigger('click')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockAgent])
  })

  it('emits click event on Enter keydown', async () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    await wrapper.trigger('keydown.enter')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockAgent])
  })

  it('emits click event on Space keydown', async () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    await wrapper.trigger('keydown.space')
    expect(wrapper.emitted('click')).toBeTruthy()
    expect(wrapper.emitted('click')![0]).toEqual([mockAgent])
  })

  it('has button role and tabindex for accessibility', () => {
    const wrapper = mount(AgentCard, { props: { agent: mockAgent } })
    const root = wrapper.find('[role="button"]')
    expect(root.exists()).toBe(true)
    expect(root.attributes('tabindex')).toBe('0')
  })

  it('renders with different agent data', () => {
    const otherAgent: AgentConfig = {
      ...mockAgent,
      name: 'other-agent',
      role: 'Designer',
      department: 'creative_marketing',
      level: 'lead',
      model: { ...mockAgent.model, model_id: 'other-model' },
    }
    const wrapper = mount(AgentCard, { props: { agent: otherAgent } })
    expect(wrapper.text()).toContain('other-agent')
    expect(wrapper.text()).toContain('Designer')
    expect(wrapper.text()).toContain('Creative Marketing')
    expect(wrapper.text()).toContain('Lead')
    expect(wrapper.text()).toContain('other-model')
  })
})

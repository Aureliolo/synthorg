import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import AgentMetrics from '@/components/agents/AgentMetrics.vue'
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

describe('AgentMetrics', () => {
  it('renders agent role', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Developer')
  })

  it('renders formatted department', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Engineering')
  })

  it('renders formatted level', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Senior')
  })

  it('renders model id', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('test-model')
  })

  it('renders formatted status', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Active')
  })

  it('renders "Default" when autonomy_level is null', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Default')
  })

  it('renders formatted autonomy level when set', () => {
    const agentWithAutonomy: AgentConfig = { ...mockAgent, autonomy_level: 'semi' }
    const wrapper = mount(AgentMetrics, { props: { agent: agentWithAutonomy } })
    expect(wrapper.text()).toContain('Semi')
  })

  it('renders personality traits', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Medium')       // risk_tolerance
    expect(wrapper.text()).toContain('High')          // creativity
    expect(wrapper.text()).toContain('Analytical')    // decision_making
  })

  it('renders personality section heading', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Personality')
  })

  it('renders tools list with tool names', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('file_read')
  })

  it('renders tools count', () => {
    const wrapper = mount(AgentMetrics, { props: { agent: mockAgent } })
    expect(wrapper.text()).toContain('Tools (1)')
  })

  it('shows "No tools configured" when tools list is empty', () => {
    const agentNoTools: AgentConfig = {
      ...mockAgent,
      tools: { ...mockAgent.tools, allowed: [] },
    }
    const wrapper = mount(AgentMetrics, { props: { agent: agentNoTools } })
    expect(wrapper.text()).toContain('No tools configured')
  })

  it('renders multiple tools', () => {
    const agentMultiTools: AgentConfig = {
      ...mockAgent,
      tools: { ...mockAgent.tools, allowed: ['file_read', 'file_write', 'git'] },
    }
    const wrapper = mount(AgentMetrics, { props: { agent: agentMultiTools } })
    expect(wrapper.text()).toContain('Tools (3)')
    expect(wrapper.text()).toContain('file_read')
    expect(wrapper.text()).toContain('file_write')
    expect(wrapper.text()).toContain('git')
  })
})

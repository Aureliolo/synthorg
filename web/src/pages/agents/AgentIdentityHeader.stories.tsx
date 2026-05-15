import type { Meta, StoryObj } from '@storybook/react'
import { AgentIdentityHeader } from './AgentIdentityHeader'
import type { AgentConfig } from '@/api/types/agents'

function makeAgent(overrides: Partial<AgentConfig> = {}): AgentConfig {
  return {
    id: 'agent-001',
    name: 'Alice Smith',
    role: 'Senior Backend Engineer',
    department: 'engineering',
    level: 'senior',
    status: 'active',
    personality: {
      traits: ['analytical'], communication_style: 'direct', risk_tolerance: 'medium',
      creativity: 'high', description: 'test', openness: 0.8, conscientiousness: 0.7,
      extraversion: 0.5, agreeableness: 0.6, stress_response: 0.9,
      decision_making: 'analytical', collaboration: 'team', verbosity: 'balanced',
      conflict_approach: 'collaborate',
    },
    model: { provider: 'test-provider', model_id: 'test-large-001', temperature: 0.7, max_tokens: 4096, fallback_model: null },
    memory: { type: 'persistent', retention_days: null },
    tools: { access_level: 'standard', allowed: ['git'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-01-15T00:00:00Z',
    ...overrides,
  }
}

const meta = {
  title: 'Agents/AgentIdentityHeader',
  component: AgentIdentityHeader,
  decorators: [(Story) => <div className="p-6 max-w-2xl"><Story /></div>],
} satisfies Meta<typeof AgentIdentityHeader>

export default meta
type Story = StoryObj<typeof meta>

export const Active: Story = { args: { agent: makeAgent() } }
export const OnLeave: Story = { args: { agent: makeAgent({ status: 'on_leave' }) } }
export const Terminated: Story = { args: { agent: makeAgent({ status: 'terminated' }) } }
export const NoAutonomy: Story = { args: { agent: makeAgent({ autonomy_level: null }) } }
export const CSuite: Story = { args: { agent: makeAgent({ level: 'c_suite', role: 'Chief Technology Officer', autonomy_level: 'full' }) } }

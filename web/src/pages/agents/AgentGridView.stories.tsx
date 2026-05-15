import type { Meta, StoryObj } from '@storybook/react'
import { MemoryRouter } from 'react-router'
import { AgentGridView } from './AgentGridView'
import type { AgentConfig } from '@/api/types/agents'

function makeAgent(overrides: Partial<AgentConfig>): AgentConfig {
  return {
    id: 'agent-001',
    name: 'Alice Smith',
    role: 'Software Engineer',
    department: 'engineering',
    level: 'senior',
    status: 'active',
    personality: {
      traits: ['analytical'],
      communication_style: 'direct',
      risk_tolerance: 'medium',
      creativity: 'high',
      description: 'test',
      openness: 0.8,
      conscientiousness: 0.7,
      extraversion: 0.5,
      agreeableness: 0.6,
      stress_response: 0.9,
      decision_making: 'analytical',
      collaboration: 'team',
      verbosity: 'balanced',
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

const agents: AgentConfig[] = [
  makeAgent({ id: '1', name: 'Alice Smith', role: 'Backend Engineer', department: 'engineering', level: 'senior', status: 'active' }),
  makeAgent({ id: '2', name: 'Bob Jones', role: 'UI Designer', department: 'design', level: 'mid', status: 'active' }),
  makeAgent({ id: '3', name: 'Carol Xu', role: 'Tech Lead', department: 'engineering', level: 'lead', status: 'active' }),
  makeAgent({ id: '4', name: 'Dave Park', role: 'SRE', department: 'operations', level: 'junior', status: 'onboarding' }),
  makeAgent({ id: '5', name: 'Eve Garcia', role: 'Security Analyst', department: 'security', level: 'senior', status: 'on_leave' }),
  makeAgent({ id: '6', name: 'Frank Lee', role: 'Data Scientist', department: 'data_analytics', level: 'mid', status: 'terminated' }),
]

const meta = {
  title: 'Agents/AgentGridView',
  component: AgentGridView,
  parameters: { a11y: { test: 'error' } },
  decorators: [
    (Story) => (
      <MemoryRouter>
        <div className="p-6 max-w-5xl">
          <Story />
        </div>
      </MemoryRouter>
    ),
  ],
} satisfies Meta<typeof AgentGridView>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: { agents },
}

export const Empty: Story = {
  args: { agents: [] },
}

export const SingleAgent: Story = {
  args: { agents: [agents[0]!] },
}

export const ManyAgents: Story = {
  args: {
    agents: Array.from({ length: 12 }, (_, i) =>
      makeAgent({
        id: `agent-${i}`,
        name: `Agent ${String.fromCodePoint(65 + i)} Smith`,
        role: 'Engineer',
        department: 'engineering',
        status: i % 4 === 0 ? 'onboarding' : 'active',
      }),
    ),
  },
}

import type { Meta, StoryObj } from '@storybook/react'
import { AgentsTab } from './AgentsTab'
import type { AgentConfig } from '@/api/types/agents'
import type { CompanyConfig } from '@/api/types/org'

const mockAgents: AgentConfig[] = [
  {
    id: 'agent-alice',
    name: 'alice',
    role: 'Lead Developer',
    department: 'engineering',
    level: 'lead',
    status: 'active',
    personality: {
      traits: ['analytical'], communication_style: 'direct',
      risk_tolerance: 'medium', creativity: 'medium', description: 'Test',
      openness: 0.7, conscientiousness: 0.8, extraversion: 0.5,
      agreeableness: 0.6, stress_response: 0.5, decision_making: 'analytical',
      collaboration: 'team', verbosity: 'balanced', conflict_approach: 'collaborate',
    },
    model: { provider: 'test-provider', model_id: 'test-medium-001', temperature: 0.7, max_tokens: 4096, fallback_model: null },
    memory: { type: 'persistent', retention_days: null },
    tools: { access_level: 'standard', allowed: ['code_edit'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-03-01T00:00:00Z',
  },
  {
    id: 'agent-bob',
    name: 'bob',
    role: 'QA Engineer',
    department: 'engineering',
    level: 'mid',
    status: 'active',
    personality: {
      traits: ['methodical'], communication_style: 'formal',
      risk_tolerance: 'low', creativity: 'low', description: 'Test',
      openness: 0.5, conscientiousness: 0.9, extraversion: 0.3,
      agreeableness: 0.7, stress_response: 0.6, decision_making: 'analytical',
      collaboration: 'team', verbosity: 'terse', conflict_approach: 'compromise',
    },
    model: { provider: 'test-provider', model_id: 'test-small-001', temperature: 0.5, max_tokens: 2048, fallback_model: null },
    memory: { type: 'persistent', retention_days: null },
    tools: { access_level: 'standard', allowed: ['code_edit'], denied: [] },
    authority: {},
    autonomy_level: 'supervised',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-03-10T00:00:00Z',
  },
  {
    id: 'agent-carol',
    name: 'carol',
    role: 'Product Manager',
    department: 'product',
    level: 'senior',
    status: 'onboarding',
    personality: {
      traits: ['creative'], communication_style: 'friendly',
      risk_tolerance: 'medium', creativity: 'high', description: 'Test',
      openness: 0.8, conscientiousness: 0.7, extraversion: 0.8,
      agreeableness: 0.8, stress_response: 0.4, decision_making: 'intuitive',
      collaboration: 'team', verbosity: 'balanced', conflict_approach: 'collaborate',
    },
    model: { provider: 'test-provider', model_id: 'test-large-001', temperature: 0.8, max_tokens: 8192, fallback_model: null },
    memory: { type: 'persistent', retention_days: null },
    tools: { access_level: 'standard', allowed: ['search'], denied: [] },
    authority: {},
    autonomy_level: 'semi',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-03-05T00:00:00Z',
  },
]

const mockConfig: CompanyConfig = {
  company_name: 'Acme Corp',
  agents: mockAgents,
  departments: [
    {
      name: 'engineering',
      display_name: 'Engineering',
      autonomy_level: null,
      budget_percent: 0,
      ceremony_policy: null,
      head: null,
      head_id: null,
      policies: {
        approval_chains: [],
        review_requirements: {
          min_reviewers: 0,
          required_reviewer_roles: [],
          self_review_allowed: true,
        },
      },
      reporting_lines: [],
      teams: [],
    },
    {
      name: 'product',
      display_name: 'Product',
      autonomy_level: null,
      budget_percent: 0,
      ceremony_policy: null,
      head: null,
      head_id: null,
      policies: {
        approval_chains: [],
        review_requirements: {
          min_reviewers: 0,
          required_reviewer_roles: [],
          self_review_allowed: true,
        },
      },
      reporting_lines: [],
      teams: [],
    },
  ],
}

const meta = {
  title: 'OrgEdit/AgentsTab',
  component: AgentsTab,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    config: mockConfig,
    saving: false,
    onCreateAgent: async () => mockAgents[0]!,
    onUpdateAgent: async () => mockAgents[0]!,
    onDeleteAgent: async () => {},
    onReorderAgents: async () => {},
    optimisticReorderAgents: () => () => {},
  },
} satisfies Meta<typeof AgentsTab>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Empty: Story = {
  args: { config: null },
}

export const Saving: Story = {
  args: { saving: true },
}

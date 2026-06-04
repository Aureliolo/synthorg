import type { Meta, StoryObj } from '@storybook/react'
import { DepartmentsTab } from './DepartmentsTab'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { CompanyConfig } from '@/api/types/org'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const mockConfig: CompanyConfig = {
  company_name: 'Acme Corp',
  agents: [
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
  ],
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
      teams: [{ name: 'backend', lead: 'alice', members: ['alice'] }],
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

const mockHealths: DepartmentHealth[] = [
  { department_name: 'engineering', agent_count: 1, active_agent_count: 1, currency: DEFAULT_CURRENCY, avg_performance_score: 7.5, department_cost_7d: 10.0, cost_trend: [], collaboration_score: 6.0, utilization_percent: 85 },
  { department_name: 'product', agent_count: 0, active_agent_count: 0, currency: DEFAULT_CURRENCY, avg_performance_score: null, department_cost_7d: 0, cost_trend: [], collaboration_score: null, utilization_percent: 60 },
]

const meta = {
  title: 'OrgEdit/DepartmentsTab',
  component: DepartmentsTab,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    config: mockConfig,
    departmentHealths: mockHealths,
    saving: false,
    onCreateDepartment: () => Promise.resolve(mockConfig.departments[0]!),
    onUpdateDepartment: () => Promise.resolve(mockConfig.departments[0]!),
    onDeleteDepartment: () => Promise.resolve(true),
    onReorderDepartments: () => Promise.resolve(true),
    optimisticReorderDepartments: () => () => {},
    onCreateTeam: (_d, data) =>
      Promise.resolve({
        name: data.name,
        lead: data.lead,
        members: data.members,
      }),
    onUpdateTeam: (_d, _t, data) =>
      Promise.resolve({
        name: data.name ?? _t,
        lead: data.lead ?? 'Unassigned',
        members: data.members ?? [data.lead ?? 'Unassigned'],
      }),
    onDeleteTeam: () => Promise.resolve(true),
    onReorderTeams: () => Promise.resolve(true),
  },
} satisfies Meta<typeof DepartmentsTab>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Empty: Story = {
  args: { config: null },
}

import type { Meta, StoryObj } from '@storybook/react'
import { DepartmentEditDrawer } from './DepartmentEditDrawer'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { CompanyConfig, Department } from '@/api/types/org'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

const mockDept: Department = {
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
  teams: [
    { name: 'Backend', lead: 'alice', members: ['alice', 'bob'] },
    { name: 'Frontend', lead: 'carol', members: ['carol'] },
  ],
}

const mockConfig: CompanyConfig = {
  company_name: 'Test Company',
  departments: [mockDept],
  agents: [],
}

const mockHealth: DepartmentHealth = {
  department_name: 'engineering',
  agent_count: 3,
  active_agent_count: 2,
  currency: DEFAULT_CURRENCY,
  avg_performance_score: 7.5,
  department_cost_7d: 25.5,
  cost_trend: [],
  collaboration_score: 6.0,
  utilization_percent: 85,
}

const meta = {
  title: 'OrgEdit/DepartmentEditDrawer',
  component: DepartmentEditDrawer,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onClose: () => {},
    department: mockDept,
    health: mockHealth,
    config: mockConfig,
    onUpdate: () => Promise.resolve(mockDept),
    onDelete: () => Promise.resolve(true),
    onCreateTeam: (_d, data) =>
      Promise.resolve({
        name: data.name ?? 'New Team',
        lead: data.lead ?? 'Unassigned',
        members: data.members ?? [data.lead ?? 'Unassigned'],
      }),
    onUpdateTeam: (_d, _t, data) =>
      Promise.resolve({
        name: data.name ?? _t,
        lead: data.lead ?? 'Unassigned',
        members: data.members ?? [],
      }),
    onDeleteTeam: () => Promise.resolve(true),
    onReorderTeams: () => Promise.resolve(true),
    saving: false,
  },
} satisfies Meta<typeof DepartmentEditDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Open: Story = {}

export const NoHealthData: Story = {
  args: { health: null },
}

export const Saving: Story = {
  args: { saving: true },
}

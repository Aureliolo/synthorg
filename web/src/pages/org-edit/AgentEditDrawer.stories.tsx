import type { Meta, StoryObj } from '@storybook/react'
import { AgentEditDrawer } from './AgentEditDrawer'
import type { AgentConfig } from '@/api/types/agents'
import type { Department } from '@/api/types/org'

const mockAgent: AgentConfig = {
  id: 'agent-alice',
  name: 'alice',
  role: 'Lead Developer',
  department: 'engineering',
  status: 'active',
  model: { provider: 'test-provider', model_id: 'test-capable-001', temperature: 0.7, max_tokens: 4096 },
  memory: { type: 'persistent', retention_days: null },
  tools: { access_level: 'standard', allowed: ['code_edit'], denied: [] },
  authority: {},
  autonomy_level: 'semi',
  strategic_output_mode: null,
  capability: null,
  model_requirement: null,
  model_capabilities: null,
  model_capability_status: 'unresolved',
  hiring_date: '2026-03-01T00:00:00Z',
}

const baseDept = {
  autonomy_level: null,
  budget_percent: 0,
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
} satisfies Partial<Department>

const mockDepartments: Department[] = [
  { ...baseDept, name: 'engineering', display_name: 'Engineering', teams: [] },
  { ...baseDept, name: 'product', display_name: 'Product', teams: [] },
]

const meta = {
  title: 'OrgEdit/AgentEditDrawer',
  component: AgentEditDrawer,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onClose: () => {},
    agent: mockAgent,
    departments: mockDepartments,
    onUpdate: () => Promise.resolve(mockAgent),
    onDelete: () => Promise.resolve(true),
    saving: false,
  },
} satisfies Meta<typeof AgentEditDrawer>

export default meta
type Story = StoryObj<typeof meta>

export const Open: Story = {}

export const Closed: Story = {
  args: { open: false },
}

export const Saving: Story = {
  args: { saving: true },
}

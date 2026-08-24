import type { Meta, StoryObj } from '@storybook/react'
import { AgentCreateDialog } from './AgentCreateDialog'
import type { AgentConfig } from '@/api/types/agents'
import type { Department } from '@/api/types/org'

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

const stubAgent: AgentConfig = {
  id: 'agent-stub',
  name: 'stub',
  role: 'Developer',
  department: 'engineering',
  status: 'active',
  model: { provider: 'test-provider', model_id: 'test-capable-001', temperature: 0.7, max_tokens: 4096 },
  memory: { type: 'persistent', retention_days: null },
  tools: { access_level: 'standard', allowed: [], denied: [] },
  authority: {},
  autonomy_level: 'semi',
  strategic_output_mode: null,
  capability: null,
  model_requirement: null,
  model_capabilities: null,
  model_capability_status: 'unresolved',
  hiring_date: '2026-03-01T00:00:00Z',
}

const meta = {
  title: 'OrgEdit/AgentCreateDialog',
  component: AgentCreateDialog,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    open: true,
    onOpenChange: () => {},
    departments: [
      { ...baseDept, name: 'engineering', display_name: 'Engineering', teams: [] },
      { ...baseDept, name: 'product', display_name: 'Product', teams: [] },
    ],
    onCreate: () => Promise.resolve(stubAgent),
  },
} satisfies Meta<typeof AgentCreateDialog>

export default meta
type Story = StoryObj<typeof meta>

export const Open: Story = {}

export const Closed: Story = {
  args: { open: false },
}

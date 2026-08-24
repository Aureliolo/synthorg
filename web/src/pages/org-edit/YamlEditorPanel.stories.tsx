import type { Meta, StoryObj } from '@storybook/react'
import { YamlEditorPanel } from './YamlEditorPanel'
import type { CompanyConfig } from '@/api/types/org'

const mockConfig: CompanyConfig = {
  company_name: 'Acme Corp',
  autonomy_level: 'semi',
  budget_monthly: 5000,
  communication_pattern: 'hierarchical',
  agents: [
    {
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
    },
  ],
  departments: [
    {
      name: 'engineering',
      display_name: 'Engineering',
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
      teams: [],
    },
  ],
}

const meta = {
  title: 'OrgEdit/YamlEditorPanel',
  component: YamlEditorPanel,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    config: mockConfig,
    onSave: () => Promise.resolve(true),
    saving: false,
  },
} satisfies Meta<typeof YamlEditorPanel>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const Saving: Story = {
  args: { saving: true },
}

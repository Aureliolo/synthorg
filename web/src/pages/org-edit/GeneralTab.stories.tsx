import type { Meta, StoryObj } from '@storybook/react'
import { GeneralTab } from './GeneralTab'
import type { CompanyConfig } from '@/api/types/org'

const mockConfig: CompanyConfig = {
  company_name: 'Acme Corp',
  agents: [],
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
  ],
}

const meta = {
  title: 'OrgEdit/GeneralTab',
  component: GeneralTab,
  parameters: {
    a11y: { test: 'error' },
  },
  args: {
    config: mockConfig,
    onUpdate: async () => true,
    saving: false,
  },
} satisfies Meta<typeof GeneralTab>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const EmptyConfig: Story = {
  args: { config: null },
}

export const Saving: Story = {
  args: { saving: true },
}

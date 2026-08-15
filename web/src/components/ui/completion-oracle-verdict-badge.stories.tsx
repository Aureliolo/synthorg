import type { Meta, StoryObj } from '@storybook/react'
import { CompletionOracleVerdictBadge } from './completion-oracle-verdict-badge'

const meta = {
  title: 'UI/CompletionOracleVerdictBadge',
  component: CompletionOracleVerdictBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof CompletionOracleVerdictBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Approved: Story = {
  args: { verdict: 'approve' },
}

export const ApprovedWithNotes: Story = {
  args: { verdict: 'approve_with_notes' },
}

export const Rejected: Story = {
  args: { verdict: 'reject' },
}

export const Escalated: Story = {
  args: { verdict: 'escalate' },
}

export const AllVerdicts: Story = {
  args: { verdict: 'approve' },
  render: () => (
    <div className="flex flex-wrap gap-2">
      <CompletionOracleVerdictBadge verdict="approve" />
      <CompletionOracleVerdictBadge verdict="approve_with_notes" />
      <CompletionOracleVerdictBadge verdict="reject" />
      <CompletionOracleVerdictBadge verdict="escalate" />
    </div>
  ),
}

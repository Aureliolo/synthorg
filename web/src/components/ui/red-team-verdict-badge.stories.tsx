import type { Meta, StoryObj } from '@storybook/react'
import { RedTeamVerdictBadge } from './red-team-verdict-badge'

const meta = {
  title: 'UI/RedTeamVerdictBadge',
  component: RedTeamVerdictBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof RedTeamVerdictBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Passed: Story = {
  args: { verdict: 'pass' },
}

export const PassedWithFindings: Story = {
  args: { verdict: 'pass_with_findings' },
}

export const Blocked: Story = {
  args: { verdict: 'block' },
}

export const AllVerdicts: Story = {
  args: { verdict: 'pass' },
  render: () => (
    <div className="flex flex-wrap gap-2">
      <RedTeamVerdictBadge verdict="pass" />
      <RedTeamVerdictBadge verdict="pass_with_findings" />
      <RedTeamVerdictBadge verdict="block" />
    </div>
  ),
}

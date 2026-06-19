import type { Meta, StoryObj } from '@storybook/react'
import { MeetingStatusBadge } from './meeting-status-badge'

const meta = {
  title: 'UI/MeetingStatusBadge',
  component: MeetingStatusBadge,
  args: { status: 'in_progress' },
} satisfies Meta<typeof MeetingStatusBadge>

export default meta
type Story = StoryObj<typeof meta>

export const InProgress: Story = {}

export const Scheduled: Story = { args: { status: 'scheduled' } }

export const Completed: Story = { args: { status: 'completed' } }

export const Failed: Story = { args: { status: 'failed' } }

export const Cancelled: Story = { args: { status: 'cancelled' } }

export const BudgetExhausted: Story = { args: { status: 'budget_exhausted' } }

export const AllStatuses: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <MeetingStatusBadge status="scheduled" />
      <MeetingStatusBadge status="in_progress" />
      <MeetingStatusBadge status="completed" />
      <MeetingStatusBadge status="failed" />
      <MeetingStatusBadge status="cancelled" />
      <MeetingStatusBadge status="budget_exhausted" />
    </div>
  ),
}

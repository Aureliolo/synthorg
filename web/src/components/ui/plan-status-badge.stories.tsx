import type { Meta, StoryObj } from '@storybook/react'

import { PLAN_STATUS_VALUES } from '@/api/types/plans'

import { PlanStatusBadge } from './plan-status-badge'

const meta = {
  title: 'UI/PlanStatusBadge',
  component: PlanStatusBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof PlanStatusBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Planning: Story = {
  args: { status: 'planning' },
}

export const Draft: Story = {
  args: { status: 'draft' },
}

export const PendingReview: Story = {
  args: { status: 'pending_review' },
}

export const Approved: Story = {
  args: { status: 'approved' },
}

export const Skeleton: Story = {
  args: { status: 'skeleton' },
}

export const Executing: Story = {
  args: { status: 'executing' },
}

export const Integrating: Story = {
  args: { status: 'integrating' },
}

export const Evaluating: Story = {
  args: { status: 'evaluating' },
}

export const Completed: Story = {
  args: { status: 'completed' },
}

export const Rejected: Story = {
  args: { status: 'rejected' },
}

export const Superseded: Story = {
  args: { status: 'superseded' },
}

export const Failed: Story = {
  args: { status: 'failed' },
}

export const AllStatuses: Story = {
  args: { status: 'pending_review' },
  render: () => (
    // Derived, not listed: a hand-written copy of the enum shows every status
    // it happened to know about when it was written, so a status added later
    // is missing from the one view whose whole job is showing all of them.
    <div className="flex flex-wrap gap-4">
      {PLAN_STATUS_VALUES.map((s) => (
        <PlanStatusBadge key={s} status={s} />
      ))}
    </div>
  ),
}

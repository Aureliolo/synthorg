import type { Meta, StoryObj } from '@storybook/react'

import type { PlanStatus } from '@/api/types/plans'

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
    <div className="flex flex-wrap gap-4">
      {(
        [
          'planning',
          'draft',
          'pending_review',
          'approved',
          'executing',
          'integrating',
          'evaluating',
          'completed',
          'rejected',
          'superseded',
          'failed',
        ] satisfies PlanStatus[]
      ).map((s) => (
        <PlanStatusBadge key={s} status={s} />
      ))}
    </div>
  ),
}

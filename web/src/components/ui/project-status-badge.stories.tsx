import type { Meta, StoryObj } from '@storybook/react'
import { ProjectStatusBadge } from './project-status-badge'
import type { ProjectStatus } from '@/api/types/enums'

const meta = {
  title: 'UI/ProjectStatusBadge',
  component: ProjectStatusBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof ProjectStatusBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Planning: Story = {
  args: { status: 'planning', showLabel: true },
}

export const Active: Story = {
  args: { status: 'active', showLabel: true },
}

export const Integrating: Story = {
  args: { status: 'integrating', showLabel: true },
}

export const Evaluating: Story = {
  args: { status: 'evaluating', showLabel: true },
}

export const OnHold: Story = {
  args: { status: 'on_hold', showLabel: true },
}

export const Completed: Story = {
  args: { status: 'completed', showLabel: true },
}

export const Cancelled: Story = {
  args: { status: 'cancelled', showLabel: true },
}

export const DotOnly: Story = {
  args: { status: 'active' },
}

export const AllStatuses: Story = {
  args: { status: 'planning', showLabel: true },
  render: () => (
    <div className="flex flex-wrap gap-4">
      {(
        [
          'planning',
          'active',
          'integrating',
          'evaluating',
          'on_hold',
          'failed',
          'completed',
          'cancelled',
        ] satisfies ProjectStatus[]
      ).map((s) => (
        <ProjectStatusBadge key={s} status={s} showLabel />
      ))}
    </div>
  ),
}

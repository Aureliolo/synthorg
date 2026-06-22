import type { Meta, StoryObj } from '@storybook/react'
import { StatusBadge } from './status-badge'

const meta = {
  title: 'UI/StatusBadge',
  component: StatusBadge,
  tags: ['autodocs'],
  argTypes: {
    status: {
      control: 'select',
      options: ['active', 'idle', 'error', 'offline'],
    },
    label: { control: 'boolean' },
    pulse: { control: 'boolean' },
  },
} satisfies Meta<typeof StatusBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Active: Story = {
  args: { status: 'active' },
}

export const Idle: Story = {
  args: { status: 'idle' },
}

export const Error: Story = {
  args: { status: 'error' },
}

export const Offline: Story = {
  args: { status: 'offline' },
}

export const WithLabel: Story = {
  args: { status: 'active', label: true },
}

export const WithPulse: Story = {
  args: { status: 'error', pulse: true },
}

export const Animated: Story = {
  args: { status: 'active', animated: true },
}

export const Decorative: Story = {
  args: { status: 'active', decorative: true },
}

export const CustomAriaLabel: Story = {
  args: { status: 'active', label: true, ariaLabel: 'Alice Smith - Active' },
}

export const AllStatuses: Story = {
  args: { status: 'active' },
  render: () => (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-4">
        <StatusBadge status="active" label />
        <StatusBadge status="idle" label />
        <StatusBadge status="error" label />
        <StatusBadge status="offline" label />
      </div>
      <div className="flex items-center gap-4">
        <StatusBadge status="active" label pulse />
        <StatusBadge status="error" label pulse />
      </div>
    </div>
  ),
}

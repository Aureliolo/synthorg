import type { Meta, StoryObj } from '@storybook/react'
import { ShieldAlert } from 'lucide-react'
import { StatusPill } from './status-pill'

const meta = {
  title: 'UI/StatusPill',
  component: StatusPill,
  tags: ['autodocs'],
  parameters: { layout: 'centered' },
} satisfies Meta<typeof StatusPill>

export default meta
type Story = StoryObj<typeof meta>

export const Success: Story = { args: { tone: 'success', children: 'Approved' } }
export const Warning: Story = { args: { tone: 'warning', children: 'Pending' } }
export const Danger: Story = { args: { tone: 'danger', children: 'Blocked' } }
export const Accent: Story = { args: { tone: 'accent', children: 'In review' } }
export const Neutral: Story = { args: { tone: 'text-secondary', children: 'Draft' } }

export const WithIcon: Story = {
  args: { tone: 'danger', icon: ShieldAlert, children: 'Suspicious' },
}

export const RawToneClasses: Story = {
  args: {
    toneClassName: 'border-accent/30 bg-accent/10 text-accent',
    children: 'Custom palette',
  },
}

export const AllTones: Story = {
  args: { tone: 'success', children: 'All tones' },
  render: () => (
    <div className="flex flex-wrap gap-2">
      <StatusPill tone="success">Success</StatusPill>
      <StatusPill tone="accent">Accent</StatusPill>
      <StatusPill tone="warning">Warning</StatusPill>
      <StatusPill tone="danger">Danger</StatusPill>
      <StatusPill tone="text-secondary">Neutral</StatusPill>
    </div>
  ),
}

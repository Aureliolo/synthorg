import type { Meta, StoryObj } from '@storybook/react'
import { Database, Wifi } from 'lucide-react'
import { HealthStatusRow } from './HealthStatusRow'

const meta = {
  title: 'Overlays/HealthPopover/HealthStatusRow',
  component: HealthStatusRow,
  tags: ['autodocs'],
  parameters: {
    layout: 'centered',
    a11y: { test: 'error' },
  },
  decorators: [
    (Story) => (
      <div className="w-80">
        <Story />
      </div>
    ),
  ],
} satisfies Meta<typeof HealthStatusRow>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {
  args: {
    icon: Wifi,
    label: 'Backend API',
    description: 'HTTP layer serving the dashboard and controller endpoints.',
    state: 'ok',
  },
}

export const Degraded: Story = {
  args: {
    ...Default.args,
    state: 'degraded',
    detail: 'auto-reconnecting',
  },
}

export const Down: Story = {
  args: {
    icon: Database,
    label: 'Persistence',
    description: 'Configured persistence backend. Writes and queries round-trip.',
    state: 'down',
    action: {
      label: 'Retry now',
      onClick: () => undefined,
    },
  },
}

export const Loading: Story = {
  args: {
    ...Default.args,
    state: 'loading',
  },
}

export const Empty: Story = {
  args: {
    ...Default.args,
    state: 'unknown',
  },
}

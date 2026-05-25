import type { Meta, StoryObj } from '@storybook/react'
import { HealthStatusIcon } from './HealthStatusIcon'
import type { SubsystemState } from './health-popover.utils'

const meta = {
  title: 'Overlays/HealthPopover/HealthStatusIcon',
  component: HealthStatusIcon,
  tags: ['autodocs'],
  parameters: {
    layout: 'centered',
    a11y: { test: 'error' },
  },
} satisfies Meta<typeof HealthStatusIcon>

export default meta
type Story = StoryObj<typeof meta>

export const Operational: Story = { args: { state: 'ok' } }
export const Degraded: Story = { args: { state: 'degraded' } }
export const Down: Story = { args: { state: 'down' } }
export const Loading: Story = { args: { state: 'loading' } }
export const Unknown: Story = { args: { state: 'unknown' } }

export const AllStates: Story = {
  args: { state: 'ok' },
  render: () => {
    const states: readonly SubsystemState[] = ['ok', 'degraded', 'down', 'loading', 'unknown']
    return (
      <div className="flex items-center gap-4">
        {states.map((s) => (
          <div key={s} className="flex flex-col items-center gap-1">
            <HealthStatusIcon state={s} />
            <span className="text-compact text-muted-foreground">{s}</span>
          </div>
        ))}
      </div>
    )
  },
}

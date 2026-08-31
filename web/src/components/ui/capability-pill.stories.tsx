import type { Meta, StoryObj } from '@storybook/react'
import { CapabilityPill } from './capability-pill'

const meta = {
  title: 'UI/CapabilityPill',
  component: CapabilityPill,
  tags: ['autodocs'],
} satisfies Meta<typeof CapabilityPill>

export default meta
type Story = StoryObj<typeof meta>

/** Resolved from the model card/probe -- no marker, no title. */
export const Resolved: Story = {
  args: { label: 'tools', className: 'bg-success/15 text-success', overridden: false },
}

/** Set by an operator override -- ring, "*" marker, and a title tooltip. */
export const OperatorOverride: Story = {
  args: { label: 'cached', className: 'bg-info/15 text-info', overridden: true },
}

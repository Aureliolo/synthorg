import type { Meta, StoryObj } from '@storybook/react'
import { LocalityBadge } from './locality-badge'

const meta = {
  title: 'UI/LocalityBadge',
  component: LocalityBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof LocalityBadge>

export default meta
type Story = StoryObj<typeof meta>

/** Local provider: renders the "local" pill. */
export const Local: Story = {
  args: { isLocal: true },
}

/** Remote provider: renders nothing (the pill is a positive-only signal). */
export const Remote: Story = {
  args: { isLocal: false },
}

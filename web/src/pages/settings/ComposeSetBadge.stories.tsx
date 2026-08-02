import type { Meta, StoryObj } from '@storybook/react'
import { ComposeSetBadge } from './ComposeSetBadge'

const meta = {
  title: 'Settings/ComposeSetBadge',
  component: ComposeSetBadge,
  parameters: { layout: 'centered' },
} satisfies Meta<typeof ComposeSetBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Default: Story = {}

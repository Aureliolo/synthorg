import type { Meta, StoryObj } from '@storybook/react-vite'

import { RunOutcomeBadge } from './run-outcome-badge'

const meta = {
  title: 'UI/RunOutcomeBadge',
  component: RunOutcomeBadge,
  tags: ['autodocs'],
} satisfies Meta<typeof RunOutcomeBadge>

export default meta
type Story = StoryObj<typeof meta>

export const Succeeded: Story = {
  args: { outcome: 'succeeded' },
}

export const Empty: Story = {
  args: { outcome: 'empty' },
}

export const Failed: Story = {
  args: { outcome: 'failed' },
}

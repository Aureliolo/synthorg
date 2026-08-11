import type { Meta, StoryObj } from '@storybook/react'

import { SpendMeasurabilityNotice } from './SpendMeasurabilityNotice'

const meta = {
  title: 'Pages/Budget/SpendMeasurabilityNotice',
  component: SpendMeasurabilityNotice,
  args: {
    measurability: 'unmeasurable',
  },
} satisfies Meta<typeof SpendMeasurabilityNotice>

export default meta

type Story = StoryObj<typeof meta>

export const Unmeasurable: Story = {}

export const Mixed: Story = {
  args: { measurability: 'mixed' },
}

export const Measured: Story = {
  args: { measurability: 'measured' },
}

export const Loading: Story = {
  args: { measurability: undefined },
}

import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'

import { HardCeilingHaltedBanner } from './HardCeilingHaltedBanner'

const meta = {
  title: 'Pages/Budget/HardCeilingHaltedBanner',
  component: HardCeilingHaltedBanner,
  args: {
    accumulatedCost: 1.2,
    ceilingAmount: 1.0,
    currency: 'USD',
    forecastId: '00000000-0000-0000-0000-000000000001',
    onRaiseCeiling: fn(),
  },
} satisfies Meta<typeof HardCeilingHaltedBanner>

export default meta

type Story = StoryObj<typeof meta>

export const Default: Story = {}

export const NoForecast: Story = {
  args: { forecastId: null },
}

export const Mutating: Story = {
  args: { mutating: true },
}

export const LargeOverage: Story = {
  args: { accumulatedCost: 8.4, ceilingAmount: 5.0 },
}

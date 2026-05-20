import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'

import { CostForecastApprovalCard } from './CostForecastApprovalCard'
import type { Forecast } from '@/api/types'

const baseForecast: Forecast = {
  forecast_id: '00000000-0000-0000-0000-000000000001',
  brief_hash: 'a'.repeat(64),
  estimated_cost: 0.85,
  lower_bound: 0.55,
  upper_bound: 1.15,
  currency: 'USD',
  decision: 'pending',
  decided_at: null,
  decided_by: null,
  ceiling_amount: null,
  created_at: '2026-05-20T12:00:00Z',
  updated_at: '2026-05-20T12:00:00Z',
}

const meta = {
  title: 'Components/Approvals/CostForecastApprovalCard',
  component: CostForecastApprovalCard,
  args: {
    forecast: baseForecast,
    onApprove: fn(),
    onReject: fn(),
    onOpenDetail: fn(),
  },
} satisfies Meta<typeof CostForecastApprovalCard>

export default meta

type Story = StoryObj<typeof meta>

export const Pending: Story = {}

export const Approved: Story = {
  args: {
    forecast: {
      ...baseForecast,
      decision: 'approved',
      decided_at: '2026-05-20T12:30:00Z',
      decided_by: 'aurelio',
      ceiling_amount: 1.8,
    },
  },
}

export const Rejected: Story = {
  args: {
    forecast: {
      ...baseForecast,
      decision: 'rejected',
      decided_at: '2026-05-20T12:30:00Z',
      decided_by: 'aurelio',
    },
  },
}

export const Mutating: Story = {
  args: { mutating: true },
}

export const LargeBriefForecast: Story = {
  args: {
    forecast: {
      ...baseForecast,
      estimated_cost: 12.4,
      lower_bound: 8.2,
      upper_bound: 18.6,
    },
  },
}

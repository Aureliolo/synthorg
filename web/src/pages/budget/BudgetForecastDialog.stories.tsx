import type { Meta, StoryObj } from '@storybook/react'
import { fn } from 'storybook/test'

import { BudgetForecastDialog } from './BudgetForecastDialog'
import type { Forecast } from '@/api/types'

const pendingForecast: Forecast = {
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
  halt_context: null,
  created_at: '2026-05-20T12:00:00Z',
  updated_at: '2026-05-20T12:00:00Z',
}

const approvedForecast: Forecast = {
  ...pendingForecast,
  decision: 'approved',
  decided_at: '2026-05-20T12:30:00Z',
  decided_by: 'aurelio',
  ceiling_amount: 1.8,
}

const meta = {
  title: 'Pages/Budget/BudgetForecastDialog',
  component: BudgetForecastDialog,
  args: {
    open: true,
    onOpenChange: fn(),
    onApprove: fn(),
    onReject: fn(),
  },
} satisfies Meta<typeof BudgetForecastDialog>

export default meta

type Story = StoryObj<typeof meta>

export const Pending: Story = {
  args: { forecast: pendingForecast },
}

export const Loading: Story = {
  args: { forecast: null, loading: true },
}

export const Approved: Story = {
  args: { forecast: approvedForecast },
}

export const Mutating: Story = {
  args: { forecast: pendingForecast, mutating: true },
}

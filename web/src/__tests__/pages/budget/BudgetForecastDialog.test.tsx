import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { BudgetForecastDialog } from '@/pages/budget/BudgetForecastDialog'
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

describe('BudgetForecastDialog', () => {
  it('renders the estimated cost and bounds', () => {
    render(
      <BudgetForecastDialog
        open
        onOpenChange={vi.fn()}
        forecast={baseForecast}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByText('Pre-flight cost forecast')).toBeInTheDocument()
    expect(screen.getByText(/range/i)).toBeInTheDocument()
  })

  it('calls onApprove with the parsed ceiling', () => {
    const onApprove = vi.fn()
    render(
      <BudgetForecastDialog
        open
        onOpenChange={vi.fn()}
        forecast={baseForecast}
        onApprove={onApprove}
        onReject={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /approve/i }))
    expect(onApprove).toHaveBeenCalledTimes(1)
    const passed = onApprove.mock.calls[0]?.[0] as number | null
    expect(passed).not.toBeNull()
    expect(passed).toBeGreaterThan(baseForecast.upper_bound)
  })

  it('calls onReject when the reject button is clicked', () => {
    const onReject = vi.fn()
    render(
      <BudgetForecastDialog
        open
        onOpenChange={vi.fn()}
        forecast={baseForecast}
        onApprove={vi.fn()}
        onReject={onReject}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onReject).toHaveBeenCalledTimes(1)
  })

  it('shows a skeleton when loading', () => {
    render(
      <BudgetForecastDialog
        open
        onOpenChange={vi.fn()}
        forecast={null}
        loading
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(document.querySelector('.animate-pulse')).not.toBeNull()
  })

  it('disables action buttons while mutating', () => {
    render(
      <BudgetForecastDialog
        open
        onOpenChange={vi.fn()}
        forecast={baseForecast}
        mutating
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /approve/i })).toBeDisabled()
    expect(screen.getByRole('button', { name: /reject/i })).toBeDisabled()
  })
})

import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { CostForecastApprovalCard } from '@/components/approvals/CostForecastApprovalCard'
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
  halt_context: null,
  created_at: '2026-05-20T12:00:00Z',
  updated_at: '2026-05-20T12:00:00Z',
}

describe('CostForecastApprovalCard', () => {
  it('renders the decision badge and cost band', () => {
    render(
      <CostForecastApprovalCard
        forecast={baseForecast}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.getByText('pending')).toBeInTheDocument()
    expect(screen.getByText(/range/i)).toBeInTheDocument()
  })

  it('invokes onApprove with null when the approve button is clicked', () => {
    const onApprove = vi.fn()
    render(
      <CostForecastApprovalCard
        forecast={baseForecast}
        onApprove={onApprove}
        onReject={vi.fn()}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /approve/i }))
    expect(onApprove).toHaveBeenCalledWith(null)
  })

  it('invokes onReject when the reject button is clicked', () => {
    const onReject = vi.fn()
    render(
      <CostForecastApprovalCard
        forecast={baseForecast}
        onApprove={vi.fn()}
        onReject={onReject}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /reject/i }))
    expect(onReject).toHaveBeenCalledTimes(1)
  })

  it('hides action buttons for non-pending forecasts', () => {
    render(
      <CostForecastApprovalCard
        forecast={{ ...baseForecast, decision: 'approved' }}
        onApprove={vi.fn()}
        onReject={vi.fn()}
      />,
    )
    expect(screen.queryByRole('button', { name: /approve/i })).toBeNull()
    expect(screen.queryByRole('button', { name: /reject/i })).toBeNull()
  })

  it('invokes onOpenDetail when the title is clicked', () => {
    const onOpenDetail = vi.fn()
    render(
      <CostForecastApprovalCard
        forecast={baseForecast}
        onApprove={vi.fn()}
        onReject={vi.fn()}
        onOpenDetail={onOpenDetail}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /pre-flight cost forecast/i }))
    expect(onOpenDetail).toHaveBeenCalledWith(baseForecast.forecast_id)
  })
})

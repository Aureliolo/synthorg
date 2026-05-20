import { fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'

import { HardCeilingHaltedBanner } from '@/pages/budget/HardCeilingHaltedBanner'

describe('HardCeilingHaltedBanner', () => {
  const baseProps = {
    accumulatedCost: 1.2,
    ceilingAmount: 1.0,
    currency: 'USD',
    forecastId: '00000000-0000-0000-0000-000000000001',
  }

  it('renders accumulated and ceiling amounts', () => {
    render(
      <HardCeilingHaltedBanner
        {...baseProps}
        onRaiseCeiling={vi.fn()}
      />,
    )
    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText(/Run halted/i)).toBeInTheDocument()
  })

  it('invokes onRaiseCeiling with the parsed value when valid', () => {
    const onRaiseCeiling = vi.fn()
    render(
      <HardCeilingHaltedBanner
        {...baseProps}
        onRaiseCeiling={onRaiseCeiling}
      />,
    )
    fireEvent.click(screen.getByRole('button', { name: /raise ceiling/i }))
    expect(onRaiseCeiling).toHaveBeenCalledTimes(1)
    const passed = onRaiseCeiling.mock.calls[0]?.[0] as number
    expect(passed).toBeGreaterThan(baseProps.accumulatedCost)
  })

  it('disables resume when the input is at or below accumulated cost', () => {
    const onRaiseCeiling = vi.fn()
    render(
      <HardCeilingHaltedBanner
        {...baseProps}
        onRaiseCeiling={onRaiseCeiling}
      />,
    )
    const input = screen.getByLabelText(/new hard ceiling/i)
    fireEvent.change(input, { target: { value: '0.5' } })
    expect(screen.getByRole('button', { name: /raise ceiling/i })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: /raise ceiling/i }))
    expect(onRaiseCeiling).not.toHaveBeenCalled()
  })

  it('disables the button while mutating', () => {
    render(
      <HardCeilingHaltedBanner
        {...baseProps}
        mutating
        onRaiseCeiling={vi.fn()}
      />,
    )
    expect(screen.getByRole('button', { name: /raise ceiling/i })).toBeDisabled()
  })
})

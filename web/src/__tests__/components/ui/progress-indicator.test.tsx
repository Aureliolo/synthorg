import { act, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, vi } from 'vitest'
import fc from 'fast-check'
import { ProgressIndicator } from '@/components/ui/progress-indicator'

describe('ProgressIndicator', () => {
  it('determinate: renders percent + aria-valuenow', () => {
    render(<ProgressIndicator variant="determinate" value={42} label="Training" />)
    const bar = screen.getByRole('progressbar', { name: 'Training' })
    expect(bar).toHaveAttribute('aria-valuenow', '42')
    expect(bar).toHaveAttribute('aria-valuemin', '0')
    expect(bar).toHaveAttribute('aria-valuemax', '100')
    expect(screen.getByText('42%')).toBeInTheDocument()
  })

  it('determinate: clamps value to [0, 100]', () => {
    const { rerender } = render(
      <ProgressIndicator variant="determinate" value={-5} label="Clamp" />,
    )
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '0')

    rerender(<ProgressIndicator variant="determinate" value={150} label="Clamp" />)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '100')
  })

  it('indeterminate: has aria-busy=true', () => {
    render(<ProgressIndicator variant="indeterminate" label="Loading" />)
    const bar = screen.getByRole('progressbar', { name: 'Loading' })
    expect(bar).toHaveAttribute('aria-busy', 'true')
  })

  it('stages: renders all stages with correct labels', () => {
    render(
      <ProgressIndicator
        variant="stages"
        stages={[
          { id: '1', label: 'Step One', status: 'done' },
          { id: '2', label: 'Step Two', status: 'running' },
          { id: '3', label: 'Step Three', status: 'pending' },
        ]}
      />,
    )
    expect(screen.getByText('Step One')).toBeInTheDocument()
    expect(screen.getByText('Step Two')).toBeInTheDocument()
    expect(screen.getByText('Step Three')).toBeInTheDocument()
  })

  it('stages: exposes status in aria-label', () => {
    render(
      <ProgressIndicator
        variant="stages"
        stages={[{ id: '1', label: 'X', status: 'failed' }]}
      />,
    )
    expect(screen.getByLabelText('X: failed')).toBeInTheDocument()
  })

  it('stages: renders description', () => {
    render(
      <ProgressIndicator
        variant="stages"
        stages={[{ id: '1', label: 'S', status: 'running', description: 'Epoch 2' }]}
      />,
    )
    expect(screen.getByText('Epoch 2')).toBeInTheDocument()
  })

  it('renders label and description', () => {
    render(
      <ProgressIndicator
        variant="determinate"
        value={50}
        label="Upload"
        description="2.5 MB / 5 MB"
      />,
    )
    expect(screen.getByText('Upload')).toBeInTheDocument()
    expect(screen.getByText('2.5 MB / 5 MB')).toBeInTheDocument()
  })

  it('indeterminate: renders description even when label is absent', () => {
    render(<ProgressIndicator variant="indeterminate" description="Warming up..." />)
    expect(screen.getByText('Warming up...')).toBeInTheDocument()
  })

  describe('indeterminate: startedAt + warningAfterSeconds', () => {
    beforeEach(() => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-05-13T12:00:30Z'))
    })
    afterEach(() => {
      vi.useRealTimers()
    })

    it('renders elapsed chip when startedAt provided', () => {
      const startedAt = new Date('2026-05-13T12:00:00Z')
      render(
        <ProgressIndicator
          variant="indeterminate"
          label="Training"
          startedAt={startedAt}
        />,
      )
      expect(screen.getByText('30s')).toBeInTheDocument()
    })

    it('updates elapsed chip once per second', () => {
      const startedAt = new Date('2026-05-13T12:00:00Z')
      render(
        <ProgressIndicator
          variant="indeterminate"
          label="Training"
          startedAt={startedAt}
        />,
      )
      expect(screen.getByText('30s')).toBeInTheDocument()
      act(() => {
        vi.advanceTimersByTime(2000)
      })
      expect(screen.getByText('32s')).toBeInTheDocument()
    })

    it('applies warning colour after threshold', () => {
      const startedAt = new Date('2026-05-13T12:00:00Z')
      render(
        <ProgressIndicator
          variant="indeterminate"
          label="Training"
          startedAt={startedAt}
          warningAfterSeconds={10}
        />,
      )
      const chip = screen.getByText('30s')
      expect(chip.className).toMatch(/text-warning/)
    })

    it('omits elapsed chip when startedAt is null', () => {
      render(<ProgressIndicator variant="indeterminate" label="Loading" startedAt={null} />)
      expect(screen.queryByText(/^\d+s/)).not.toBeInTheDocument()
    })

    it('clamps negative elapsed (future startedAt) to zero', () => {
      const startedAt = new Date('2026-05-13T12:01:00Z')
      render(
        <ProgressIndicator
          variant="indeterminate"
          label="Training"
          startedAt={startedAt}
        />,
      )
      expect(screen.getByText('0s')).toBeInTheDocument()
    })

    it('cleans up the interval on unmount', () => {
      const clearSpy = vi.spyOn(global, 'clearInterval')
      const startedAt = new Date('2026-05-13T12:00:00Z')
      const { unmount } = render(
        <ProgressIndicator
          variant="indeterminate"
          label="Training"
          startedAt={startedAt}
        />,
      )
      unmount()
      expect(clearSpy).toHaveBeenCalled()
      clearSpy.mockRestore()
    })
  })

  it('property: determinate clamp invariant holds for any finite value', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: -10_000, max: 10_000 }),
        (value) => {
          const { unmount } = render(
            <ProgressIndicator variant="determinate" value={value} label="Clamp" />,
          )
          const bar = screen.getByRole('progressbar', { name: 'Clamp' })
          const expected = String(Math.min(100, Math.max(0, Math.round(value))))
          expect(bar).toHaveAttribute('aria-valuenow', expected)
          unmount()
        },
      ),
      { numRuns: 20 },
    )
  })
})

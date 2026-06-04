import { render, screen } from '@testing-library/react'
import * as fc from 'fast-check'
import { MetricCard } from '@/components/ui/metric-card'

describe('MetricCard', () => {
  it('renders label text', () => {
    render(<MetricCard label="Tasks Today" value={24} />)

    expect(screen.getByText('Tasks Today')).toBeInTheDocument()
  })

  it('renders numeric value', () => {
    render(<MetricCard label="Tasks" value={24} />)

    expect(screen.getByText('24')).toBeInTheDocument()
  })

  it('renders string value', () => {
    render(<MetricCard label="Spend" value="$12.50" />)

    expect(screen.getByText('$12.50')).toBeInTheDocument()
  })

  it('renders positive change badge with up indicator', () => {
    render(<MetricCard label="Tasks" value={24} change={{ value: 12, direction: 'up' }} />)

    expect(screen.getByText(/\+12%/)).toBeInTheDocument()
  })

  it('renders negative change badge with down indicator', () => {
    render(<MetricCard label="Tasks" value={24} change={{ value: 8, direction: 'down' }} />)

    expect(screen.getByText(/-8%/)).toBeInTheDocument()
  })

  it('renders sparkline when data is provided', () => {
    const { container } = render(
      <MetricCard label="Tasks" value={24} sparklineData={[1, 2, 3, 4]} />,
    )

    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('does not render sparkline when no data', () => {
    const { container } = render(<MetricCard label="Tasks" value={24} />)

    expect(container.querySelector('svg')).not.toBeInTheDocument()
  })

  it('renders progress bar when progress is provided', () => {
    const { container } = render(
      <MetricCard label="Tasks" value={24} progress={{ current: 24, total: 30 }} />,
    )

    expect(container.querySelector('[role="progressbar"]')).toBeInTheDocument()
  })

  it('renders sub-text when provided', () => {
    render(<MetricCard label="Tasks" value={24} subText="of 30 completed" />)

    expect(screen.getByText('of 30 completed')).toBeInTheDocument()
  })

  it('applies custom className', () => {
    const { container } = render(
      <MetricCard label="Tasks" value={24} className="my-class" />,
    )

    expect(container.firstChild).toHaveClass('my-class')
  })

  it('handles progress with total=0 without division error', () => {
    const { container } = render(
      <MetricCard label="Tasks" value={24} progress={{ current: 5, total: 0 }} />,
    )

    const progressbar = container.querySelector('[role="progressbar"]')
    expect(progressbar).toBeInTheDocument()
    expect(progressbar).toHaveAttribute('aria-valuenow', '0')
  })

  it('does not render sparkline for single-element data', () => {
    const { container } = render(
      <MetricCard label="Tasks" value={24} sparklineData={[5]} />,
    )

    expect(container.querySelector('svg')).not.toBeInTheDocument()
  })

  it('renders any numeric value correctly (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: -999999, max: 999999 }), (num) => {
        const { unmount } = render(<MetricCard label="Test" value={num} />)
        expect(screen.getByText(String(num))).toBeInTheDocument()
        unmount()
      }),
    )
  })

  it('formats change badge for any percentage (property)', () => {
    fc.assert(
      fc.property(
        fc.integer({ min: 0, max: 100 }),
        fc.constantFrom('up', 'down'),
        (value, direction) => {
          const { unmount } = render(
            <MetricCard label="Test" value={0} change={{ value, direction }} />,
          )
          const prefix = direction === 'up' ? '+' : '-'
          expect(screen.getByText(`${prefix}${value}%`)).toBeInTheDocument()
          unmount()
        },
      ),
    )
  })
})

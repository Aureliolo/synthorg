import { render, screen } from '@testing-library/react'
import * as fc from 'fast-check'
import { ProgressGauge } from '@/components/ui/progress-gauge'

// The circular variant renders the percentage twice -- once as visible
// SVG ``<text>`` and once inside the SVG ``<title>`` element that
// screen readers announce. Tests use ``getAllByText`` to accept either
// match without coupling to the DOM shape.
function expectPercentageText(value: string) {
  const matches = screen.getAllByText(value)
  expect(matches.length).toBeGreaterThan(0)
}

describe.each<['circular' | 'linear']>([
  ['circular'],
  ['linear'],
])('ProgressGauge shared behavior (variant: %s)', (variant) => {
  it('renders the percentage value', () => {
    render(<ProgressGauge value={75} variant={variant} />)
    expectPercentageText('75%')
  })

  it('renders the label when provided', () => {
    render(<ProgressGauge value={50} variant={variant} label="Budget" />)
    expect(screen.getByText('Budget')).toBeInTheDocument()
  })

  it('clamps value to 0 minimum', () => {
    render(<ProgressGauge value={-10} variant={variant} />)
    expectPercentageText('0%')
  })

  it('clamps value to max', () => {
    render(<ProgressGauge value={150} max={100} variant={variant} />)
    expectPercentageText('100%')
  })

  it('computes percentage from custom max', () => {
    render(<ProgressGauge value={50} max={200} variant={variant} />)
    expectPercentageText('25%')
  })

  it('has accessible role and aria attributes', () => {
    render(<ProgressGauge value={75} variant={variant} label="CPU" />)
    const gauge = screen.getByRole('meter')
    expect(gauge).toHaveAttribute('aria-valuenow', '75')
    expect(gauge).toHaveAttribute('aria-valuemin', '0')
    expect(gauge).toHaveAttribute('aria-valuemax', '100')
  })

  it('applies custom className', () => {
    const { container } = render(
      <ProgressGauge value={50} variant={variant} className="my-class" />,
    )
    expect(container.firstChild).toHaveClass('my-class')
  })

  it('handles max=0 without NaN', () => {
    render(<ProgressGauge value={50} max={0} variant={variant} />)
    expectPercentageText('100%')
  })

  it('handles negative max by clamping to 1', () => {
    render(<ProgressGauge value={50} max={-50} variant={variant} />)
    // safeMax becomes Math.max(-50, 1) = 1, clampedValue = min(50, 1) = 1, percentage = 100%
    expectPercentageText('100%')
  })

  it('handles NaN max as 1', () => {
    render(<ProgressGauge value={50} max={NaN} variant={variant} />)
    // safeMax becomes 1, clampedValue = min(50, 1) = 1, percentage = 100%
    expectPercentageText('100%')
  })

  it('treats NaN value as 0%', () => {
    render(<ProgressGauge value={NaN} variant={variant} />)
    expectPercentageText('0%')
  })

  it('treats Infinity as 0%', () => {
    render(<ProgressGauge value={Infinity} variant={variant} />)
    expectPercentageText('0%')
  })

  it('treats -Infinity as 0%', () => {
    render(<ProgressGauge value={-Infinity} variant={variant} />)
    expectPercentageText('0%')
  })

  it('always clamps percentage between 0 and 100 (property)', () => {
    fc.assert(
      fc.property(
        fc.float({ min: -1000, max: 1000, noNaN: true }),
        fc.float({ min: 1, max: 1000, noNaN: true }),
        (value, max) => {
          const { unmount } = render(
            <ProgressGauge value={value} max={max} variant={variant} />,
          )
          const matches = screen.getAllByText(/%$/)
          expect(matches.length).toBeGreaterThan(0)
          const percentage = parseInt(matches[0]!.textContent ?? '0')
          expect(percentage).toBeGreaterThanOrEqual(0)
          expect(percentage).toBeLessThanOrEqual(100)
          unmount()
        },
      ),
    )
  })
})

describe('ProgressGauge circular variant', () => {
  it('defaults to circular variant (SVG present)', () => {
    const { container } = render(<ProgressGauge value={50} />)
    expect(container.querySelector('svg')).toBeInTheDocument()
  })

  it('renders SVG with arc', () => {
    const { container } = render(<ProgressGauge value={60} />)
    expect(container.querySelector('svg')).toBeInTheDocument()
    expect(container.querySelectorAll('path').length).toBeGreaterThanOrEqual(1)
  })

  it('renders small size variant with different dimensions', () => {
    const { container } = render(<ProgressGauge value={50} size="sm" />)
    const svg = container.querySelector('svg')
    expect(svg).toBeInTheDocument()
    // sm radius=32, stroke=6 -> svgWidth=(32+6)*2=76, md radius=48 -> svgWidth=(48+6)*2=108
    expect(svg).toHaveAttribute('width', '76')
  })
})

describe('ProgressGauge linear variant', () => {
  it('does not render SVG', () => {
    const { container } = render(<ProgressGauge value={60} variant="linear" />)
    expect(container.querySelector('svg')).not.toBeInTheDocument()
  })

  it('renders a bar track and fill', () => {
    render(<ProgressGauge value={60} variant="linear" />)
    expect(screen.getByTestId('progress-track')).toBeInTheDocument()
    expect(screen.getByTestId('progress-fill')).toBeInTheDocument()
  })

  it('has aria-label with label prop', () => {
    render(<ProgressGauge value={75} variant="linear" label="CPU" />)
    const gauge = screen.getByRole('meter')
    expect(gauge).toHaveAttribute('aria-label', 'CPU: 75%')
  })

  it('has aria-label without label prop', () => {
    render(<ProgressGauge value={60} variant="linear" />)
    const gauge = screen.getByRole('meter')
    expect(gauge).toHaveAttribute('aria-label', '60%')
  })

  it('normalizes aria-valuenow to percentage with custom max', () => {
    render(<ProgressGauge value={50} max={200} variant="linear" label="Tokens" />)
    const gauge = screen.getByRole('meter')
    expect(gauge).toHaveAttribute('aria-valuenow', '25')
    expect(gauge).toHaveAttribute('aria-valuemin', '0')
    expect(gauge).toHaveAttribute('aria-valuemax', '100')
  })

  it.each([
    { value: 10, expected: 'bg-danger' },
    { value: 24, expected: 'bg-danger' },
    { value: 25, expected: 'bg-warning' },
    { value: 35, expected: 'bg-warning' },
    { value: 49, expected: 'bg-warning' },
    { value: 50, expected: 'bg-accent' },
    { value: 60, expected: 'bg-accent' },
    { value: 74, expected: 'bg-accent' },
    { value: 75, expected: 'bg-success' },
    { value: 90, expected: 'bg-success' },
  ])('applies $expected for value=$value', ({ value, expected }) => {
    render(<ProgressGauge value={value} variant="linear" />)
    expect(screen.getByTestId('progress-fill')).toHaveClass(expected)
  })

  it('sets fill width to percentage', () => {
    render(<ProgressGauge value={75} variant="linear" />)
    expect(screen.getByTestId('progress-fill')).toHaveStyle({ width: '75%' })
  })

  it('renders sm size with smaller track', () => {
    render(<ProgressGauge value={50} variant="linear" size="sm" />)
    expect(screen.getByTestId('progress-track')).toHaveClass('h-1.5')
  })

  it('renders 0% fill width at zero value', () => {
    render(<ProgressGauge value={0} variant="linear" />)
    expect(screen.getByTestId('progress-fill')).toHaveStyle({ width: '0%' })
    expect(screen.getByText('0%')).toBeInTheDocument()
  })
})

import { render, screen } from '@testing-library/react'
import * as fc from 'fast-check'
import { DeptHealthBar } from '@/components/ui/dept-health-bar'

describe('DeptHealthBar', () => {
  const defaultProps = {
    name: 'Engineering',
    health: 85,
    agentCount: 5,
  }

  it('renders department name', () => {
    render(<DeptHealthBar {...defaultProps} />)

    expect(screen.getByText('Engineering')).toBeInTheDocument()
  })

  it('renders health percentage', () => {
    render(<DeptHealthBar {...defaultProps} />)

    expect(screen.getByText('85%')).toBeInTheDocument()
  })

  it('renders agent count', () => {
    render(<DeptHealthBar {...defaultProps} />)

    expect(screen.getByText(/5 agents/)).toBeInTheDocument()
  })

  it('uses singular form for count of 1', () => {
    render(<DeptHealthBar {...defaultProps} agentCount={1} />)

    expect(screen.getByText(/1 agent$/)).toBeInTheDocument()
  })

  it('clamps health to 0-100 range', () => {
    render(<DeptHealthBar {...defaultProps} health={120} agentCount={1} />)

    expect(screen.getByText('100%')).toBeInTheDocument()
  })

  it('clamps negative health to 0', () => {
    render(<DeptHealthBar {...defaultProps} health={-20} />)

    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('handles zero health', () => {
    render(<DeptHealthBar name="Broken" health={0} agentCount={0} />)

    expect(screen.getByText('0%')).toBeInTheDocument()
  })

  it('has accessible meter role labelled as health, not utilisation', () => {
    render(<DeptHealthBar name="Eng" health={75} agentCount={3} />)

    const meter = screen.getByRole('meter')
    expect(meter).toHaveAttribute('aria-valuenow', '75')
    // The bar renders health_score, so its accessible name must say "health"
    // and never mislabel it as utilisation.
    expect(meter).toHaveAccessibleName('Eng health: 75%')
  })

  it('applies custom className', () => {
    const { container } = render(
      <DeptHealthBar name="Eng" health={75} agentCount={3} className="my-class" />,
    )

    expect(container.firstChild).toHaveClass('my-class')
  })

  it('clamps health above 100 to 100% (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 101, max: 1000 }), (health) => {
        const { unmount } = render(<DeptHealthBar {...defaultProps} health={health} />)
        expect(screen.getByText('100%')).toBeInTheDocument()
        unmount()
      }),
    )
  })

  it('clamps negative health to 0% (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: -1000, max: -1 }), (health) => {
        const { unmount } = render(<DeptHealthBar {...defaultProps} health={health} />)
        expect(screen.getByText('0%')).toBeInTheDocument()
        unmount()
      }),
    )
  })

  it('displays N/A when health is null', () => {
    render(<DeptHealthBar name="Empty" health={null} agentCount={2} />)

    expect(screen.getByText('N/A')).toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })

  it('displays N/A when health is undefined', () => {
    render(<DeptHealthBar name="Unknown" agentCount={3} />)

    expect(screen.getByText('N/A')).toBeInTheDocument()
    expect(screen.queryByRole('meter')).not.toBeInTheDocument()
  })

  it('displays health within 0-100 as-is (property)', () => {
    fc.assert(
      fc.property(fc.integer({ min: 0, max: 100 }), (health) => {
        const { unmount } = render(<DeptHealthBar {...defaultProps} health={health} />)
        expect(screen.getByText(`${health}%`)).toBeInTheDocument()
        unmount()
      }),
    )
  })
})

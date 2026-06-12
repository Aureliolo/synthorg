import { render, screen } from '@testing-library/react'
import { CategoryBreakdown } from '@/pages/budget/CategoryBreakdown'
import { formatCurrency } from '@/utils/format'
import type { CategoryRatio } from '@/utils/budget'

const EMPTY_RATIO: CategoryRatio = {
  productive: { cost: 0, percent: 0, count: 0 },
  coordination: { cost: 0, percent: 0, count: 0 },
  system: { cost: 0, percent: 0, count: 0 },
  embedding: { cost: 0, percent: 0, count: 0 },
  uncategorized: { cost: 0, percent: 0, count: 0 },
}

const BALANCED_RATIO: CategoryRatio = {
  productive: { cost: 30, percent: 33.3, count: 10 },
  coordination: { cost: 30, percent: 33.3, count: 8 },
  system: { cost: 30, percent: 33.4, count: 12 },
  embedding: { cost: 0, percent: 0, count: 0 },
  uncategorized: { cost: 0, percent: 0, count: 0 },
}

const PRODUCTIVE_HEAVY: CategoryRatio = {
  productive: { cost: 75, percent: 75, count: 20 },
  coordination: { cost: 10, percent: 10, count: 5 },
  system: { cost: 5, percent: 5, count: 3 },
  embedding: { cost: 5, percent: 5, count: 2 },
  uncategorized: { cost: 5, percent: 5, count: 2 },
}

describe('CategoryBreakdown', () => {
  it('renders section title', () => {
    render(<CategoryBreakdown ratio={EMPTY_RATIO} />)
    expect(screen.getByText('Cost Categories')).toBeInTheDocument()
  })

  it('shows empty state when all costs are zero', () => {
    render(<CategoryBreakdown ratio={EMPTY_RATIO} />)
    expect(screen.getByText('No cost data')).toBeInTheDocument()
    expect(screen.getByText('Category breakdown will appear as agents consume tokens.')).toBeInTheDocument()
  })

  it('renders stacked bar segments', () => {
    render(<CategoryBreakdown ratio={BALANCED_RATIO} />)
    expect(screen.getByTestId('bar-productive')).toBeInTheDocument()
    expect(screen.getByTestId('bar-coordination')).toBeInTheDocument()
    expect(screen.getByTestId('bar-system')).toBeInTheDocument()
    expect(screen.getByTestId('bar-embedding')).toBeInTheDocument()
    expect(screen.getByTestId('bar-uncategorized')).toBeInTheDocument()
  })

  it('sets correct width percentages on bar segments', () => {
    render(<CategoryBreakdown ratio={PRODUCTIVE_HEAVY} />)
    expect(screen.getByTestId('bar-productive')).toHaveStyle({ width: '75%' })
    expect(screen.getByTestId('bar-coordination')).toHaveStyle({ width: '10%' })
    expect(screen.getByTestId('bar-system')).toHaveStyle({ width: '5%' })
    expect(screen.getByTestId('bar-embedding')).toHaveStyle({ width: '5%' })
    expect(screen.getByTestId('bar-uncategorized')).toHaveStyle({ width: '5%' })
  })

  it('renders legend with all five category labels', () => {
    render(<CategoryBreakdown ratio={BALANCED_RATIO} />)
    expect(screen.getByText('Productive')).toBeInTheDocument()
    expect(screen.getByText('Coordination')).toBeInTheDocument()
    expect(screen.getByText('System')).toBeInTheDocument()
    expect(screen.getByText('Embedding')).toBeInTheDocument()
    expect(screen.getByText('Uncategorized')).toBeInTheDocument()
  })

  it('renders formatted currency values in legend', () => {
    render(<CategoryBreakdown ratio={PRODUCTIVE_HEAVY} />)
    expect(screen.getByText(formatCurrency(75))).toBeInTheDocument()
    expect(screen.getByText(formatCurrency(10))).toBeInTheDocument()
  })

  it('renders percentage values in legend', () => {
    render(<CategoryBreakdown ratio={PRODUCTIVE_HEAVY} />)
    expect(screen.getByText('75.0%')).toBeInTheDocument()
    expect(screen.getByText('10.0%')).toBeInTheDocument()
    expect(screen.getAllByText('5.0%')).toHaveLength(3)
  })

  it('uses specified currency', () => {
    render(<CategoryBreakdown ratio={PRODUCTIVE_HEAVY} currency="USD" />)
    expect(screen.getByText(formatCurrency(75, 'USD'))).toBeInTheDocument()
    expect(screen.getByText(formatCurrency(10, 'USD'))).toBeInTheDocument()
  })

  it('does not show empty state when any cost is non-zero', () => {
    const ratio: CategoryRatio = {
      productive: { cost: 1, percent: 100, count: 1 },
      coordination: { cost: 0, percent: 0, count: 0 },
      system: { cost: 0, percent: 0, count: 0 },
      embedding: { cost: 0, percent: 0, count: 0 },
      uncategorized: { cost: 0, percent: 0, count: 0 },
    }
    render(<CategoryBreakdown ratio={ratio} />)
    expect(screen.queryByText('No cost data')).not.toBeInTheDocument()
  })
})

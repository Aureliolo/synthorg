import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { makePlanItem } from '@/__tests__/helpers/factories'
import { PlanTimeline } from '@/pages/plans/PlanTimeline'

describe('PlanTimeline', () => {
  it('renders waves and flags the parallel one', () => {
    const items = [
      makePlanItem('a', { title: 'Architecture', dependencies: [] }),
      makePlanItem('b', { title: 'Engine', dependencies: ['a'], owner: 'CTO' }),
      makePlanItem('c', { title: 'Leaderboard', dependencies: ['a'] }),
    ]
    render(<PlanTimeline items={items} />)
    expect(screen.getByText('Execution timeline')).toBeInTheDocument()
    expect(screen.getByText('Wave 1')).toBeInTheDocument()
    expect(screen.getByText('Wave 2')).toBeInTheDocument()
    // b and c both depend only on a, so wave 2 runs them in parallel.
    expect(screen.getByText('2 in parallel')).toBeInTheDocument()
    expect(screen.getByText('Engine')).toBeInTheDocument()
  })

  it('renders nothing when the plan has a single wave', () => {
    const { container } = render(
      <PlanTimeline items={[makePlanItem('a'), makePlanItem('b')]} />,
    )
    expect(container).toBeEmptyDOMElement()
  })
})

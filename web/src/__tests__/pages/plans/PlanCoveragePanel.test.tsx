import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlanCoveragePanel } from '@/pages/plans/PlanCoveragePanel'

import { makePlan, makePlanItem } from '../../helpers/factories'

describe('PlanCoveragePanel', () => {
  it('renders nothing when the objective declared no criteria', () => {
    const { container } = render(
      <PlanCoveragePanel plan={makePlan('p', { objective_criteria: [] })} />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows covered criteria with their items and flags uncovered ones', () => {
    const plan = makePlan('p', {
      objective_criteria: ['Playable board', 'Score tracking'],
      items: [makePlanItem('a', { title: 'Board', satisfies: ['Playable board'] })],
    })
    render(<PlanCoveragePanel plan={plan} />)
    expect(screen.getByText('Success-criteria coverage')).toBeInTheDocument()
    expect(screen.getByText('1/2 covered')).toBeInTheDocument()
    expect(screen.getByText(/Advanced by Board/)).toBeInTheDocument()
    expect(screen.getByText('No item advances this criterion.')).toBeInTheDocument()
  })
})

import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlanOpenQuestionsPanel } from '@/pages/plans/PlanOpenQuestionsPanel'

import { makePlan } from '../../helpers/factories'

describe('PlanOpenQuestionsPanel', () => {
  it('renders nothing when the plan surfaced no questions or assumptions', () => {
    const { container } = render(<PlanOpenQuestionsPanel plan={makePlan('p')} />)
    expect(container).toBeEmptyDOMElement()
  })

  it('lists open questions with a count and the plan assumptions', () => {
    const plan = makePlan('p', {
      open_questions: ['Which persistence backend?', 'Is offline play in scope?'],
      assumptions: ['Single-player only for v1'],
    })
    render(<PlanOpenQuestionsPanel plan={plan} />)
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.getByText('2 open questions')).toBeInTheDocument()
    expect(screen.getByText('Which persistence backend?')).toBeInTheDocument()
    expect(screen.getByText('Single-player only for v1')).toBeInTheDocument()
  })

  it('shows assumptions alone without an open-question count', () => {
    render(
      <PlanOpenQuestionsPanel
        plan={makePlan('p', { assumptions: ['Metric units throughout'] })}
      />,
    )
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.queryByText(/open question/)).not.toBeInTheDocument()
    expect(screen.getByText('Metric units throughout')).toBeInTheDocument()
  })
})

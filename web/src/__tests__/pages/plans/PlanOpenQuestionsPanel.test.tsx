import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import type { Plan } from '@/api/types/plans'
import { PlanOpenQuestionsPanel } from '@/pages/plans/PlanOpenQuestionsPanel'

import { makePlan, makePlanItem } from '../../helpers/factories'

function renderPanel(plan: Plan) {
  return render(
    <MemoryRouter>
      <PlanOpenQuestionsPanel plan={plan} />
    </MemoryRouter>,
  )
}

describe('PlanOpenQuestionsPanel', () => {
  it('renders nothing when the plan surfaced no questions or assumptions', () => {
    const { container } = renderPanel(makePlan('p'))
    expect(container).toBeEmptyDOMElement()
  })

  it('lists open questions with a count and the plan assumptions', () => {
    const plan = makePlan('p', {
      open_questions: ['Which persistence backend?', 'Is offline play in scope?'],
      assumptions: ['Single-player only for v1'],
    })
    renderPanel(plan)
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.getByText('2 open questions')).toBeInTheDocument()
    expect(screen.getByText('Which persistence backend?')).toBeInTheDocument()
    expect(screen.getByText('Single-player only for v1')).toBeInTheDocument()
  })

  it('shows assumptions alone without an open-question count', () => {
    renderPanel(makePlan('p', { assumptions: ['Metric units throughout'] }))
    expect(screen.getByText('Needs your input')).toBeInTheDocument()
    expect(screen.queryByText(/open question/)).not.toBeInTheDocument()
    expect(screen.getByText('Metric units throughout')).toBeInTheDocument()
  })

  it('points at where an answer is given', () => {
    // The panel was read-only with no affordance: an operator could see what
    // was wanted and not where to say it.
    renderPanel(makePlan('p', { open_questions: ['Which persistence backend?'] }))
    expect(screen.getByRole('link', { name: /answer in chat/i })).toHaveAttribute(
      'href',
      '/chat',
    )
  })

  it('stops asking a question the plan already settles', () => {
    const plan = makePlan('p', {
      open_questions: ['Which persistence backend?', 'Is offline play in scope?'],
      items: [
        makePlanItem('i1', {
          title: 'Storage layer',
          acceptance_criteria: ['The persistence backend is SQLite'],
        }),
      ],
    })
    renderPanel(plan)

    // Only the genuinely open one counts, and only it gets the ask.
    expect(screen.getByText('1 open question')).toBeInTheDocument()
    expect(screen.getByText('Already answered by the plan')).toBeInTheDocument()
    // Separated, not deleted: a wrong match must cost a glance rather than a
    // question the operator never got to answer.
    expect(screen.getByText(/settled by Storage layer/)).toBeInTheDocument()
  })
})

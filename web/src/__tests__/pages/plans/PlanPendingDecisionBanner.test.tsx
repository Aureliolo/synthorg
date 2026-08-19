import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PlanPendingDecisionBanner } from '@/pages/plans/PlanPendingDecisionBanner'

import { makePlan } from '../../helpers/factories'

const DECISION = {
  approval_id: 'approval-1',
  action_type: 'initiative:stalled',
  title: 'Initiative stopped: Ship the Tetris game',
  reason: 'This initiative can no longer advance: all failed.',
  requested_by: 'initiative-rollup',
} as const

function renderBanner(plan: ReturnType<typeof makePlan>) {
  render(
    <MemoryRouter>
      <PlanPendingDecisionBanner plan={plan} />
    </MemoryRouter>,
  )
}

describe('PlanPendingDecisionBanner', () => {
  it('says the initiative is waiting on the reader', () => {
    // The status badge answers a different question, so a plan that ran out
    // of automatic recovery reads "executing" while nothing executes.
    renderBanner(
      makePlan('plan-1', { status: 'executing', pending_decision: DECISION }),
    )

    expect(screen.getByText(DECISION.title)).toBeInTheDocument()
    expect(
      screen.getByText(`${DECISION.reason} Raised by ${DECISION.requested_by}.`),
    ).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Answer it' })).toHaveAttribute(
      'href',
      '/approvals?selected=approval-1',
    )
  })

  it('names who raised the decision', () => {
    // Title and reason are prose the raiser chose, and the queue accepts an
    // item from anything with write access, so the requester is the one field
    // that tells a decision the organisation raised from one it did not.
    renderBanner(
      makePlan('plan-3', {
        status: 'executing',
        pending_decision: { ...DECISION, requested_by: 'someone-else' },
      }),
    )

    expect(screen.getByText(/Raised by someone-else\./)).toBeInTheDocument()
  })

  it('renders nothing for a plan with no decision waiting', () => {
    const { container } = render(
      <MemoryRouter>
        <PlanPendingDecisionBanner
          plan={makePlan('plan-2', { status: 'executing' })}
        />
      </MemoryRouter>,
    )

    expect(container).toBeEmptyDOMElement()
  })
})

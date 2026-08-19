import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PlanListItem } from '@/pages/plans/PlanListItem'

import { makePlan } from '../../helpers/factories'

const DECISION = {
  approval_id: 'approval-1',
  action_type: 'initiative:stalled',
  title: 'Initiative stopped: Ship the Tetris game',
  reason: 'This initiative can no longer advance: all failed.',
  requested_by: 'initiative-rollup',
} as const

function renderRow(plan: ReturnType<typeof makePlan>) {
  render(
    <MemoryRouter>
      <PlanListItem plan={plan} roster={undefined} />
    </MemoryRouter>,
  )
}

describe('PlanListItem', () => {
  it('says the row is waiting on the reader', () => {
    // The status pill answers "what did the org last do"; a plan whose every
    // item is dead still reads "executing", which is the board telling the
    // operator work is in flight when none is.
    renderRow(makePlan('plan-1', { status: 'executing', pending_decision: DECISION }))

    expect(screen.getByText('Awaiting your decision')).toBeInTheDocument()
    expect(screen.getByText(DECISION.reason)).toBeInTheDocument()
  })

  it('says nothing for a plan with no decision waiting', () => {
    renderRow(makePlan('plan-2', { status: 'executing' }))

    expect(screen.queryByText('Awaiting your decision')).not.toBeInTheDocument()
  })

  it('keeps the plan status beside it rather than replacing it', () => {
    // Nothing is hidden: the badge still says what the plan's own status is,
    // and the pill adds what the status cannot express.
    renderRow(makePlan('plan-3', { status: 'executing', pending_decision: DECISION }))

    expect(screen.getByText('Awaiting your decision')).toBeInTheDocument()
    expect(screen.getByText(/executing/i)).toBeInTheDocument()
  })
})

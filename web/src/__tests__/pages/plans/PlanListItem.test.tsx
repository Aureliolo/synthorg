import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import { PlanListItem } from '@/pages/plans/PlanListItem'
import { criticalPathFor, derivePlanStats } from '@/utils/plans'

import { makePlan, makePlanItem } from '../../helpers/factories'

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

/**
 * A branching plan whose longest chain is a strict subset, so critical-path
 * membership is a real flag rather than a degenerate one covering every item.
 * The chain items are owned, so without the critical path they carry no flag
 * at all and the two derivations differ by exactly that.
 */
function branchingPlan(id: string, overrides?: Parameters<typeof makePlan>[1]) {
  return makePlan(id, {
    task_structure: 'mixed',
    items: [
      makePlanItem('root', { owner: 'Developer' }),
      makePlanItem('middle', { owner: 'Developer', dependencies: ['root'] }),
      makePlanItem('leaf', { owner: 'Developer', dependencies: ['middle'] }),
      makePlanItem('aside', { owner: 'Developer' }),
    ],
    ...overrides,
  })
}

describe('PlanListItem review solicitation', () => {
  it('offers the count a reviewer can still act on', () => {
    renderRow(makePlan('plan-open', { status: 'pending_review' }))

    expect(screen.getByText(/to review/)).toBeInTheDocument()
  })

  it('asks for nothing on a superseded plan', () => {
    // The revision has been replaced. Its items still carry their flags, so
    // the row advertised a review of a decision that had already been taken
    // and could not be retaken here.
    renderRow(makePlan('plan-old', { status: 'superseded' }))

    expect(screen.queryByText(/to review/)).not.toBeInTheDocument()
  })

  it('asks for nothing on a completed plan', () => {
    renderRow(makePlan('plan-done', { status: 'completed' }))

    expect(screen.queryByText(/to review/)).not.toBeInTheDocument()
  })

  it('counts what the detail page counts, critical path included', () => {
    // One number, one label, two surfaces. The row read 3 while its own
    // detail page headlined 6, because the row derived its stats against an
    // empty critical path.
    const plan = branchingPlan('plan-branching')
    const expected = derivePlanStats(
      plan.items,
      criticalPathFor(plan.items, plan.task_structure),
      undefined,
    ).flaggedItems

    renderRow(plan)

    expect(expected).toBeGreaterThan(0)
    expect(screen.getByText(`${expected} to review`)).toBeInTheDocument()
  })
})

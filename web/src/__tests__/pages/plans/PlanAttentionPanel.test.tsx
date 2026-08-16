import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlanAttentionPanel } from '@/pages/plans/PlanAttentionPanel'

import { makePlanItem } from '../../helpers/factories'

const CLEAN = makePlanItem('a', {
  owner: 'Backend Developer',
  stakes: 'normal',
  estimated_complexity: 'medium',
  acceptance_criteria: ['builds green'],
})

describe('PlanAttentionPanel', () => {
  it('says a plan with no items has none, rather than clearing it for review', () => {
    // "Nothing flagged" is vacuously true over zero items, and reading it as
    // a clean bill of health invites a decision on an undrafted plan.
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="draft"
      />,
    )

    expect(screen.getByText('This plan has no items yet')).toBeInTheDocument()
    expect(screen.queryByText(/make your decision/)).not.toBeInTheDocument()
  })

  it('tells a waiting operator that planning is still running', () => {
    // The plan's own status distinguishes "still being written" from
    // "planning failed" exactly; asserting both left nothing to act on.
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="planning"
      />,
    )

    expect(screen.getByText('Planning is still running')).toBeInTheDocument()
    expect(screen.queryByText(/did not produce/)).not.toBeInTheDocument()
  })

  it('tells an operator whose planning failed that nothing is coming', () => {
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="failed"
      />,
    )

    expect(screen.getByText('Planning did not produce a plan')).toBeInTheDocument()
    expect(screen.queryByText(/still running/)).not.toBeInTheDocument()
  })

  it('clears a plan whose items are all owned and scoped', () => {
    render(
      <PlanAttentionPanel
        items={[CLEAN]}
        criticalPath={new Set()}
        roster={undefined}
        status="pending_review"
      />,
    )

    expect(screen.getByText('Nothing flagged for review')).toBeInTheDocument()
  })

  it('flags an item whose owner no agent holds', () => {
    const invented = makePlanItem('b', {
      owner: 'Backend Engineer',
      stakes: 'normal',
      estimated_complexity: 'medium',
      acceptance_criteria: ['builds green'],
    })

    render(
      <PlanAttentionPanel
        items={[CLEAN, invented]}
        criticalPath={new Set()}
        roster={new Set(['Backend Developer'])}
        status="pending_review"
      />,
    )

    expect(screen.getByText('Needs your attention')).toBeInTheDocument()
    expect(screen.getByText('Owner not in the org')).toBeInTheDocument()
  })
})

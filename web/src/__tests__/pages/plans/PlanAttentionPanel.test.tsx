import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import type { DecompositionProgress } from '@/api/types/plans'
import { PlanAttentionPanel } from '@/pages/plans/PlanAttentionPanel'

import { makePlanItem } from '../../helpers/factories'

const CLEAN = makePlanItem('a', {
  owner: 'Backend Developer',
  stakes: 'normal',
  estimated_complexity: 'medium',
  acceptance_criteria: ['builds green'],
})

function progress(overrides: Partial<DecompositionProgress> = {}): DecompositionProgress {
  return {
    sessions_spent: 7,
    sessions_limit: 40,
    deepest_level: 0,
    units_planned: 12,
    updated_at: '2026-08-26T12:00:00Z',
    ...overrides,
  }
}

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
        decompositionProgress={null}
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
        decompositionProgress={null}
      />,
    )

    expect(screen.getByText('Planning is still running')).toBeInTheDocument()
    expect(screen.queryByText(/did not produce/)).not.toBeInTheDocument()
  })

  it('never promises items will appear as they are written', () => {
    // The whole defect: the tree is persisted in one pass at the end, so the
    // item count is zero for the entire run. A live run held an operator at
    // zero for 54 minutes under a sentence saying items were arriving.
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="planning"
        decompositionProgress={null}
      />,
    )

    expect(screen.queryByText(/appear as they are written/)).not.toBeInTheDocument()
    expect(screen.getByText(/one pass at the end/)).toBeInTheDocument()
  })

  it('reports how far the decomposition has got when it has reported', () => {
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="planning"
        decompositionProgress={progress()}
      />,
    )

    // All three numbers, because each answers a different question: which
    // level it reached, how much it has produced, and how much budget is
    // left before the tree stops on its own.
    expect(screen.getByText(/the first level/)).toBeInTheDocument()
    expect(screen.getByText(/12 units/)).toBeInTheDocument()
    expect(screen.getByText(/7 of 40 planning sessions/)).toBeInTheDocument()
  })

  it('counts levels from one for a reader, not from zero', () => {
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="planning"
        decompositionProgress={progress({ deepest_level: 2, units_planned: 1 })}
      />,
    )

    expect(screen.getByText(/level 3/)).toBeInTheDocument()
    // Singular, so a one-unit tree does not read "1 units".
    expect(screen.getByText(/1 unit written/)).toBeInTheDocument()
  })

  it('tells an operator whose planning failed that nothing is coming', () => {
    render(
      <PlanAttentionPanel
        items={[]}
        criticalPath={new Set()}
        roster={undefined}
        status="failed"
        decompositionProgress={null}
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
        decompositionProgress={null}
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
        decompositionProgress={null}
      />,
    )

    expect(screen.getByText('Needs your attention')).toBeInTheDocument()
    expect(screen.getByText('Owner not in the org')).toBeInTheDocument()
  })
})

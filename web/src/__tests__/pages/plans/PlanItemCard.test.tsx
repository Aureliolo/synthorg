import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { PlanItemCard } from '@/pages/plans/PlanItemCard'

import { makePlanItem } from '../../helpers/factories'

const NO_TITLES: ReadonlyMap<string, string> = new Map()

describe('PlanItemCard', () => {
  it('renders a work item without decision affordances', () => {
    render(
      <PlanItemCard
        item={makePlanItem('item-1')}
        index={0}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.queryByText('Decision')).not.toBeInTheDocument()
    expect(screen.queryByText('Options')).not.toBeInTheDocument()
  })

  it('flags a decision item and lists its options with recommended and chosen badges', () => {
    const item = makePlanItem('decide-1', {
      kind: 'decision',
      chosen_option_id: 'opt-b',
      options: [
        { id: 'opt-a', title: 'Postgres', summary: 'Relational store.', recommended: true },
        { id: 'opt-b', title: 'SQLite', summary: 'Embedded store.', recommended: false },
      ],
    })
    render(
      <PlanItemCard
        item={item}
        index={2}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.getByText('Decision')).toBeInTheDocument()
    expect(screen.getByText('Options')).toBeInTheDocument()
    expect(screen.getByText('Postgres')).toBeInTheDocument()
    expect(screen.getByText('SQLite')).toBeInTheDocument()
    expect(screen.getByText('Recommended')).toBeInTheDocument()
    expect(screen.getByText('Chosen')).toBeInTheDocument()
  })
})

import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { PlanItemCard } from '@/pages/plans/PlanItemCard'

import { makePlanItem } from '../../helpers/factories'

const NO_TITLES: ReadonlyMap<string, string> = new Map()

function makeDecision() {
  return makePlanItem('decide-1', {
    kind: 'decision',
    chosen_option_id: 'opt-b',
    options: [
      { id: 'opt-a', title: 'Postgres', summary: 'Relational store.', recommended: true },
      { id: 'opt-b', title: 'SQLite', summary: 'Embedded store.', recommended: false },
    ],
  })
}

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
    render(
      <PlanItemCard
        item={makeDecision()}
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

  it('offers a Choose action only on unchosen options when editable', async () => {
    const onChooseOption = vi.fn().mockResolvedValue(undefined)
    render(
      <PlanItemCard
        item={makeDecision()}
        index={2}
        onCriticalPath={false}
        titleById={NO_TITLES}
        onChooseOption={onChooseOption}
      />,
    )
    // One Choose button (each labelled by its option): the already-chosen
    // option (SQLite) shows none.
    const choose = screen.getAllByRole('button', { name: /^Choose / })
    expect(choose).toHaveLength(1)
    await userEvent.click(choose[0]!)
    expect(onChooseOption).toHaveBeenCalledWith('decide-1', 'opt-a')
  })

  it('shows no Choose action when read-only', () => {
    render(
      <PlanItemCard
        item={makeDecision()}
        index={2}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.queryByRole('button', { name: /^Choose / })).not.toBeInTheDocument()
  })
})

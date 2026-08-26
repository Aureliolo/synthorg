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
        label="1"
        depth={0}
        childCount={0}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.queryByText('Decision')).not.toBeInTheDocument()
    expect(screen.queryByText('Options')).not.toBeInTheDocument()
  })

  it('reads a container as an assembly of what it was split into', () => {
    render(
      <PlanItemCard
        item={makePlanItem('engine')}
        label="2"
        depth={0}
        childCount={3}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.getByText('Assembles 3')).toBeInTheDocument()
  })

  it('shows the bound that stopped a split, so the reviewer can move it', () => {
    // Left in a log it reaches nobody who can raise the bound or narrow the
    // objective, which are the only two remedies.
    render(
      <PlanItemCard
        item={makePlanItem('wide', {
          unsplit_reason: 'Still more than one agent’s work: the depth backstop was reached.',
        })}
        label="4"
        depth={1}
        childCount={0}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.getByText(/depth backstop was reached/)).toBeInTheDocument()
  })

  it('numbers a nested item by its position in the tree', () => {
    render(
      <PlanItemCard
        item={makePlanItem('leaf', { title: 'Grid renderer' })}
        label="2.1.3"
        depth={2}
        childCount={0}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.getByRole('heading', { name: '2.1.3. Grid renderer' })).toBeInTheDocument()
  })

  it('flags a decision item and lists its options with recommended and chosen badges', () => {
    render(
      <PlanItemCard
        item={makeDecision()}
        label="3"
        depth={0}
        childCount={0}
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
        label="3"
        depth={0}
        childCount={0}
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
        label="3"
        depth={0}
        childCount={0}
        onCriticalPath={false}
        titleById={NO_TITLES}
      />,
    )
    expect(screen.queryByRole('button', { name: /^Choose / })).not.toBeInTheDocument()
  })
})

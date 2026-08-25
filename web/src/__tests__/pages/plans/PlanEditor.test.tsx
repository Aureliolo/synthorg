import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { PlanEditor } from '@/pages/plans/PlanEditor'
import { usePlansStore } from '@/stores/plans'
import { server } from '@/test-setup'

import { makePlan, makePlanItem } from '../../helpers/factories'

function resetStore(): void {
  usePlansStore.getState().reset()
}

const plan = makePlan('plan-1', {
  items: [makePlanItem('i1', { title: 'Scaffold', description: 'Board' })],
})

describe('PlanEditor', () => {
  it('adds and removes items', async () => {
    resetStore()
    const user = userEvent.setup()
    render(<PlanEditor plan={plan} roster={undefined} onDone={vi.fn()} />)

    expect(screen.getByText('Item 1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Add item/ }))
    expect(screen.getByText('Item 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Remove item 2/ }))
    expect(screen.queryByText('Item 2')).not.toBeInTheDocument()
  })

  it('disables save when an item title is blank', async () => {
    resetStore()
    const user = userEvent.setup()
    render(<PlanEditor plan={plan} roster={undefined} onDone={vi.fn()} />)

    const titleInput = screen.getByDisplayValue('Scaffold')
    await user.clear(titleInput)
    expect(screen.getByRole('button', { name: /Save revision/ })).toBeDisabled()
  })

  it('disables save when an item has no acceptance criterion', async () => {
    resetStore()
    const user = userEvent.setup()
    const withCriterion = makePlan('plan-1', {
      items: [
        makePlanItem('i1', {
          title: 'Scaffold',
          description: 'Board',
          acceptance_criteria: ['board renders'],
        }),
      ],
    })
    render(<PlanEditor plan={withCriterion} roster={undefined} onDone={vi.fn()} />)

    // The backend requires at least one acceptance criterion per item, so
    // clearing the last one disables save rather than round-tripping to a 422.
    expect(screen.getByRole('button', { name: /Save revision/ })).toBeEnabled()
    await user.clear(screen.getByDisplayValue('board renders'))
    expect(screen.getByRole('button', { name: /Save revision/ })).toBeDisabled()
  })

  it('saves the revision and calls onDone', async () => {
    resetStore()
    const onDone = vi.fn()
    const revised = makePlan('plan-1', { version: 2 })
    server.use(
      http.patch('/api/v1/plans/:id', () => HttpResponse.json(apiSuccess(revised))),
    )
    const user = userEvent.setup()
    render(<PlanEditor plan={plan} roster={undefined} onDone={onDone} />)

    await user.click(screen.getByRole('button', { name: /Save revision/ }))
    await waitFor(() => {
      expect(onDone).toHaveBeenCalledOnce()
    })
  })

  it('offers the staffed roles as owner choices', () => {
    resetStore()
    render(
      <PlanEditor
        plan={plan}
        roster={new Set(['Backend Developer', 'Designer'])}
        onDone={vi.fn()}
      />,
    )

    const owner = screen.getByLabelText('Owner (role)')
    expect(owner).toHaveRole('combobox')
    const offered = [...(owner as HTMLSelectElement).options].map((o) => o.value)
    expect(offered).toEqual(['', 'Backend Developer', 'Designer'])
  })

  it('flags an owner no agent holds and blocks the save', () => {
    resetStore()
    const invented = makePlan('plan-1', {
      items: [makePlanItem('i1', { title: 'Scaffold', owner: 'Backend Engineer' })],
    })
    render(
      <PlanEditor
        plan={invented}
        roster={new Set(['Backend Developer'])}
        onDone={vi.fn()}
      />,
    )

    // The near-miss the decomposer invented is exactly what the backend
    // refuses, so the editor names it rather than letting the save 422.
    expect(screen.getByRole('alert')).toHaveTextContent(
      'No agent holds the role "Backend Engineer"',
    )
    expect(screen.getByRole('button', { name: /Save revision/ })).toBeDisabled()
  })

  it('leaves the owner free text while the roster is unknown', () => {
    resetStore()
    render(<PlanEditor plan={plan} roster={undefined} onDone={vi.fn()} />)

    expect(screen.getByLabelText('Owner (role)')).toHaveRole('textbox')
  })

  describe('containment', () => {
    const tree = makePlan('plan-1', {
      items: [
        makePlanItem('engine', { title: 'Engine' }),
        makePlanItem('board', { title: 'Board', parent_id: 'engine' }),
        makePlanItem('grid', { title: 'Grid', parent_id: 'board' }),
        makePlanItem('pick', { title: 'Pick a store', kind: 'decision' }),
      ],
    })

    function parentChoicesFor(index: number): readonly string[] {
      const fields = screen.getAllByLabelText('Belongs to')
      const field = fields[index]
      if (field === undefined) throw new Error(`no row ${String(index)}`)
      return [...(field as HTMLSelectElement).options].map((option) => option.value)
    }

    it('shows what each item currently belongs to', () => {
      resetStore()
      render(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      expect(screen.getAllByLabelText('Belongs to')[1]).toHaveValue('engine')
    })

    it('refuses to offer an item its own subtree as a parent', () => {
      // Choosing one would close a containment cycle, which the backend
      // rejects: better never offered than refused after a round trip.
      resetStore()
      render(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      expect(parentChoicesFor(0)).not.toContain('engine')
      expect(parentChoicesFor(0)).not.toContain('board')
      expect(parentChoicesFor(0)).not.toContain('grid')
    })

    it('refuses to offer a decision as a parent', () => {
      // A decision is chosen rather than decomposed, so nothing can hang off
      // one: dispatch strips it and its children would be orphaned.
      resetStore()
      render(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      expect(parentChoicesFor(1)).not.toContain('pick')
    })

    it('promotes an orphaned child when its container is removed', async () => {
      // Left naming a parent the plan no longer holds, the save would 422.
      resetStore()
      const user = userEvent.setup()
      render(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: /Remove item 2/ }))

      // Board is gone; Grid now sits where Board did, under Engine.
      expect(screen.getAllByLabelText('Belongs to')[1]).toHaveValue('engine')
    })

    it('sends the parent link back on save', async () => {
      resetStore()
      let sent: unknown = null
      server.use(
        http.patch('/api/v1/plans/:id', async ({ request }) => {
          sent = await request.json()
          return HttpResponse.json(apiSuccess(makePlan('plan-1', { version: 2 })))
        }),
      )
      const user = userEvent.setup()
      render(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: /Save revision/ }))

      await waitFor(() => {
        expect(sent).not.toBeNull()
      })
      const items = (sent as { items: readonly { id: string; parent_id: string | null }[] })
        .items
      expect(items.map((item) => [item.id, item.parent_id])).toEqual([
        ['engine', null],
        ['board', 'engine'],
        ['grid', 'board'],
        ['pick', null],
      ])
    })
  })
})

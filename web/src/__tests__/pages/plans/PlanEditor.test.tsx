import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import type { ReactElement } from 'react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import { apiSuccess } from '@/mocks/handlers'
import { PlanEditor } from '@/pages/plans/PlanEditor'
import { ROWS_PER_PAGE } from '@/pages/plans/PlanEditor.paging'
import { usePlansStore } from '@/stores/plans'
import { server } from '@/test-setup'

import { makePlan, makePlanItem } from '../../helpers/factories'

function resetStore(): void {
  usePlansStore.getState().reset()
}

/**
 * Render the editor inside a router.
 *
 * The row list is paged and the pager keeps its page in the URL, so a deep
 * link reopens where the operator left off. That needs a router context.
 */
function renderEditor(ui: ReactElement): void {
  render(<MemoryRouter>{ui}</MemoryRouter>)
}

const plan = makePlan('plan-1', {
  items: [makePlanItem('i1', { title: 'Scaffold', description: 'Board' })],
})

describe('PlanEditor', () => {
  it('adds and removes items', async () => {
    resetStore()
    const user = userEvent.setup()
    renderEditor(<PlanEditor plan={plan} roster={undefined} onDone={vi.fn()} />)

    expect(screen.getByText('Item 1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Add item/ }))
    expect(screen.getByText('Item 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Remove item 2/ }))
    expect(screen.queryByText('Item 2')).not.toBeInTheDocument()
  })

  it('disables save when an item title is blank', async () => {
    resetStore()
    const user = userEvent.setup()
    renderEditor(<PlanEditor plan={plan} roster={undefined} onDone={vi.fn()} />)

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
    renderEditor(<PlanEditor plan={withCriterion} roster={undefined} onDone={vi.fn()} />)

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
    renderEditor(<PlanEditor plan={plan} roster={undefined} onDone={onDone} />)

    await user.click(screen.getByRole('button', { name: /Save revision/ }))
    await waitFor(() => {
      expect(onDone).toHaveBeenCalledOnce()
    })
  })

  it('drops blank deliverable lines from the saved revision', async () => {
    // Both list fields are a textarea split on newlines, so a trailing one
    // leaves an empty entry the backend refuses as a 422 after the round trip.
    resetStore()
    const revised = makePlan('plan-1', { version: 2 })
    let sent: { items: { expected_artifacts: string[] }[] } | undefined
    server.use(
      http.patch('/api/v1/plans/:id', async ({ request }) => {
        sent = (await request.json()) as typeof sent
        return HttpResponse.json(apiSuccess(revised))
      }),
    )
    const user = userEvent.setup()
    const withArtifact = makePlan('plan-1', {
      items: [
        makePlanItem('i1', {
          title: 'Scaffold',
          acceptance_criteria: ['board renders'],
          expected_artifacts: ['src/board.ts'],
        }),
      ],
    })
    renderEditor(<PlanEditor plan={withArtifact} roster={undefined} onDone={vi.fn()} />)

    // Found by its value rather than its label: a required field renders a
    // marker after the label text, so the accessible name is not the string
    // the component was given.
    await user.type(screen.getByDisplayValue('src/board.ts'), '\n')
    await user.click(screen.getByRole('button', { name: /Save revision/ }))

    await waitFor(() => {
      expect(sent?.items[0]?.expected_artifacts).toEqual(['src/board.ts'])
    })
  })

  it('offers the staffed roles as owner choices', () => {
    resetStore()
    renderEditor(
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
    renderEditor(
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
    renderEditor(<PlanEditor plan={plan} roster={undefined} onDone={vi.fn()} />)

    expect(screen.getByLabelText('Owner (role)')).toHaveRole('textbox')
  })

  describe('paging', () => {
    // Sized off the page rather than a literal, so tuning the page size stays
    // a judgement about what an operator can scan rather than a test edit.
    // One row past a full second page keeps a partial last page in play.
    const OVERFLOW = ROWS_PER_PAGE + 5
    const FIRST_ON_PAGE_TWO = ROWS_PER_PAGE + 1

    // Titles deliberately unlike the "Item N" row headers, so a header
    // assertion cannot be satisfied by a title that happens to read the same.
    const many = makePlan('plan-1', {
      items: Array.from({ length: OVERFLOW }, (_, index) =>
        makePlanItem(`i${String(index + 1)}`, { title: `Task ${String(index + 1)}` }),
      ),
    })

    it('holds one page of rows on screen rather than the whole plan', () => {
      // A row is a whole form, and its container picker offers every item in
      // the plan. Rendering all of them at the thousand items the backend
      // accepts is around a million option elements before anyone types.
      resetStore()
      renderEditor(<PlanEditor plan={many} roster={undefined} onDone={vi.fn()} />)

      expect(screen.getAllByLabelText('Belongs to')).toHaveLength(ROWS_PER_PAGE)
      expect(screen.getByDisplayValue(`Task ${String(ROWS_PER_PAGE)}`)).toBeInTheDocument()
      expect(
        screen.queryByDisplayValue(`Task ${String(FIRST_ON_PAGE_TWO)}`),
      ).not.toBeInTheDocument()
    })

    it('numbers the rows by their place in the plan, not in the page', async () => {
      // The number is how the operator refers to an item, so it has to mean
      // the same thing on page two as on page one.
      resetStore()
      const user = userEvent.setup()
      renderEditor(<PlanEditor plan={many} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: 'Next page' }))

      expect(
        screen.getByDisplayValue(`Task ${String(FIRST_ON_PAGE_TWO)}`),
      ).toBeInTheDocument()
      expect(screen.getByText(`Item ${String(FIRST_ON_PAGE_TWO)}`)).toBeInTheDocument()
      expect(screen.queryByText('Item 1')).not.toBeInTheDocument()
    })

    it('follows a newly added item onto its own page', async () => {
      // Appended to the end, which is not the page being read, so an add that
      // stayed put would look like it had done nothing.
      resetStore()
      const user = userEvent.setup()
      renderEditor(<PlanEditor plan={many} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: /Add item/ }))

      expect(screen.getByText(`Item ${String(OVERFLOW + 1)}`)).toBeInTheDocument()
    })

    it('still gates the save on an item the operator has paged away from', async () => {
      // The paged-away row is what a 422 would come back about, so a gate that
      // only saw the page would let the operator try and be refused.
      resetStore()
      const user = userEvent.setup()
      renderEditor(<PlanEditor plan={many} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: 'Next page' }))
      await user.clear(screen.getByDisplayValue(`Task ${String(FIRST_ON_PAGE_TWO)}`))
      await user.click(screen.getByRole('button', { name: 'Previous page' }))

      expect(screen.getByDisplayValue('Task 1')).toBeInTheDocument()
      expect(screen.getByRole('button', { name: /Save revision/ })).toBeDisabled()
    })
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
      renderEditor(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      expect(screen.getAllByLabelText('Belongs to')[1]).toHaveValue('engine')
    })

    it('refuses to offer an item its own subtree as a parent', () => {
      // Choosing one would close a containment cycle, which the backend
      // rejects: better never offered than refused after a round trip.
      resetStore()
      renderEditor(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      expect(parentChoicesFor(0)).not.toContain('engine')
      expect(parentChoicesFor(0)).not.toContain('board')
      expect(parentChoicesFor(0)).not.toContain('grid')
    })

    it('refuses to offer a decision as a parent', () => {
      // A decision is chosen rather than decomposed, so nothing can hang off
      // one: dispatch strips it and its children would be orphaned.
      resetStore()
      renderEditor(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      expect(parentChoicesFor(1)).not.toContain('pick')
    })

    it('promotes an orphaned child when its container is removed', async () => {
      // Left naming a parent the plan no longer holds, the save would 422.
      resetStore()
      const user = userEvent.setup()
      renderEditor(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: /Remove item 2/ }))

      // Board is gone; Grid now sits where Board did, under Engine.
      expect(screen.getAllByLabelText('Belongs to')[1]).toHaveValue('engine')
    })

    it('drops the removed item from what other items wait on', async () => {
      // Containment is only half of what points at a removed item. There is
      // no dependency field in this editor, so an edge left naming it is a
      // guaranteed 422 the operator has no way in here to clear.
      resetStore()
      const waiting = makePlan('plan-1', {
        items: [
          makePlanItem('engine', { title: 'Engine' }),
          makePlanItem('board', { title: 'Board', dependencies: ['engine'] }),
        ],
      })
      let sent: unknown = null
      server.use(
        http.patch('/api/v1/plans/:id', async ({ request }) => {
          sent = await request.json()
          return HttpResponse.json(apiSuccess(makePlan('plan-1', { version: 2 })))
        }),
      )
      const user = userEvent.setup()
      renderEditor(<PlanEditor plan={waiting} roster={undefined} onDone={vi.fn()} />)

      await user.click(screen.getByRole('button', { name: /Remove item 1/ }))
      await user.click(screen.getByRole('button', { name: /Save revision/ }))

      await waitFor(() => {
        expect(sent).not.toBeNull()
      })
      const items = (
        sent as { items: readonly { id: string; dependencies: readonly string[] }[] }
      ).items
      expect(items).toHaveLength(1)
      expect(items[0]?.dependencies).toEqual([])
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
      renderEditor(<PlanEditor plan={tree} roster={undefined} onDone={vi.fn()} />)

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

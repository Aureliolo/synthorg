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
    render(<PlanEditor plan={plan} onDone={vi.fn()} />)

    expect(screen.getByText('Item 1')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: /Add item/ }))
    expect(screen.getByText('Item 2')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /Remove item 2/ }))
    expect(screen.queryByText('Item 2')).not.toBeInTheDocument()
  })

  it('disables save when an item title is blank', async () => {
    resetStore()
    const user = userEvent.setup()
    render(<PlanEditor plan={plan} onDone={vi.fn()} />)

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
    render(<PlanEditor plan={withCriterion} onDone={vi.fn()} />)

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
    render(<PlanEditor plan={plan} onDone={onDone} />)

    await user.click(screen.getByRole('button', { name: /Save revision/ }))
    await waitFor(() => {
      expect(onDone).toHaveBeenCalledOnce()
    })
  })
})

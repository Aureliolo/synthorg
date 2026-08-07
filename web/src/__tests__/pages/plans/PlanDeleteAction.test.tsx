import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'

import type { Plan } from '@/api/types/plans'
import { apiError } from '@/mocks/handlers'
import { PlanDeleteAction } from '@/pages/plans/PlanDeleteAction'
import { usePlansStore } from '@/stores/plans'
import { server } from '@/test-setup'

import { makePlan } from '../../helpers/factories'

function renderAction(plan: Plan) {
  usePlansStore.getState().reset()
  usePlansStore.setState({ plans: [plan], selectedPlan: plan })
  return render(
    <MemoryRouter>
      <PlanDeleteAction plan={plan} />
    </MemoryRouter>,
  )
}

async function confirmDelete() {
  const user = userEvent.setup()
  await user.click(screen.getByRole('button', { name: /delete plan/i }))
  const dialog = await screen.findByRole('alertdialog')
  await user.click(within(dialog).getByRole('button', { name: /delete plan/i }))
}

describe('PlanDeleteAction', () => {
  it('deletes a plan that never became work', async () => {
    let deleted = false
    server.use(
      http.delete('/api/v1/plans/:id', () => {
        deleted = true
        return new HttpResponse(null, { status: 204 })
      }),
    )
    renderAction(makePlan('plan-1', { status: 'pending_review' }))

    await confirmDelete()

    await waitFor(() => {
      expect(deleted).toBe(true)
    })
    expect(usePlansStore.getState().plans).toEqual([])
  })

  it.each(['approved', 'executing', 'completed', 'superseded'] as const)(
    'offers no delete for a %s plan',
    (status) => {
      renderAction(makePlan('plan-1', { status }))

      expect(screen.queryByRole('button', { name: /delete plan/i })).toBeNull()
    },
  )

  it('keeps the dialog open when the API refuses', async () => {
    server.use(
      http.delete('/api/v1/plans/:id', () =>
        HttpResponse.json(apiError('Plan is dispatched'), { status: 409 }),
      ),
    )
    renderAction(makePlan('plan-1', { status: 'pending_review' }))

    await confirmDelete()

    // The refusal is read beside the action that caused it, so the dialog
    // stays up rather than closing on a delete that did not happen.
    await waitFor(() => {
      expect(screen.getByRole('alertdialog')).toBeInTheDocument()
    })
    expect(usePlansStore.getState().plans).toHaveLength(1)
  })
})

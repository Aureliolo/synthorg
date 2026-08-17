import { render, screen, waitFor } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import type { getPlanTransitions } from '@/api/endpoints/plans'
import { successFor } from '@/mocks/handlers/helpers'
import { PlanHistoryPanel } from '@/pages/plans/PlanHistoryPanel'
import { usePlanTransitionsStore } from '@/stores/planTransitions'
import { server } from '@/test-setup'

afterEach(() => {
  usePlanTransitionsStore.getState().clear()
})

describe('PlanHistoryPanel', () => {
  it('shows how the plan reached its current status', async () => {
    render(<PlanHistoryPanel planId="plan-1" />)

    await waitFor(() => {
      expect(screen.getByText(/planning → pending_review/)).toBeInTheDocument()
    })
  })

  it('names the system when nothing asked for the move', async () => {
    // A null requested_by is the reconciler or a rollup moving the plan on its
    // own schedule, which is an answer to "who", not a missing one.
    render(<PlanHistoryPanel planId="plan-1" />)

    await waitFor(() => {
      expect(screen.getByText('the system')).toBeInTheDocument()
    })
  })

  it('reads a first observed status as the plan being opened', async () => {
    server.use(
      http.get('/api/v1/plans/:id/transitions', () =>
        HttpResponse.json(
          successFor<typeof getPlanTransitions>([
            {
              id: 'transition-0',
              entity_kind: 'plan',
              entity_id: 'plan-1',
              from_status: null,
              to_status: 'planning',
              requested_by: 'operator-1',
              requested_by_name: 'Ada Chen',
              reason: 'greenlit',
              entity_version: 1,
              occurred_at: '2026-07-01T09:00:00Z',
            },
          ]),
        ),
      ),
    )

    render(<PlanHistoryPanel planId="plan-1" />)

    await waitFor(() => {
      expect(screen.getByText(/opened → planning/)).toBeInTheDocument()
    })
    // The name the backend resolved, never the reference beside it.
    expect(screen.getByText('Ada Chen')).toBeInTheDocument()
    expect(screen.queryByText('operator-1')).not.toBeInTheDocument()
    expect(screen.getByText('greenlit')).toBeInTheDocument()
  })

  it('surfaces a read failure inline rather than blanking the section', async () => {
    server.use(
      http.get('/api/v1/plans/:id/transitions', () =>
        HttpResponse.json({ error: { message: 'ledger unavailable' } }, { status: 500 }),
      ),
    )

    render(<PlanHistoryPanel planId="plan-1" />)

    await waitFor(() => {
      expect(screen.getByText(/Status history unavailable/)).toBeInTheDocument()
    })
  })
})

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import type { listApprovals } from '@/api/endpoints/approvals'
import { emptyPage, paginatedFor, successFor } from '@/mocks/handlers/helpers'
import { PlanApprovalActions } from '@/pages/plans/PlanApprovalActions'
import { server } from '@/test-setup'

import { makeApproval, makePlan } from '../../helpers/factories'

const PLAN = makePlan('plan-1', { status: 'pending_review' })

function planReviewApproval() {
  return makeApproval('appr-1', {
    source: 'plan_review',
    status: 'pending',
    metadata: { plan_id: 'plan-1' },
  })
}

describe('PlanApprovalActions', () => {
  it('resolves the approval from the scoped plan-review query and approves', async () => {
    const approveSpy = vi.fn()
    let listParams: URLSearchParams | null = null
    server.use(
      http.get('/api/v1/approvals', ({ request }) => {
        listParams = new URL(request.url).searchParams
        return HttpResponse.json(
          paginatedFor<typeof listApprovals>({
            ...emptyPage(),
            data: [planReviewApproval()],
          }),
        )
      }),
      http.post('/api/v1/approvals/appr-1/approve', () => {
        approveSpy()
        return HttpResponse.json(
          successFor<() => Promise<unknown>>(planReviewApproval()),
        )
      }),
    )
    render(<PlanApprovalActions plan={PLAN} />)
    const approveBtn = await screen.findByRole('button', { name: /approve plan/i })
    // The lookup was scoped to pending plan reviews, not the mixed inbox.
    expect(listParams!.get('source')).toBe('plan_review')
    expect(listParams!.get('status')).toBe('pending')
    await userEvent.click(approveBtn)
    await waitFor(() => {
      expect(approveSpy).toHaveBeenCalledOnce()
    })
  })

  it('offers a retry when the approval lookup fails, then recovers', async () => {
    // First lookup 500s: the controls must not silently vanish -- a retry
    // affordance appears. The retry then succeeds and the approve control shows.
    let attempt = 0
    server.use(
      http.get('/api/v1/approvals', () => {
        attempt += 1
        if (attempt === 1) {
          return HttpResponse.json({ detail: 'boom' }, { status: 500 })
        }
        return HttpResponse.json(
          paginatedFor<typeof listApprovals>({
            ...emptyPage(),
            data: [planReviewApproval()],
          }),
        )
      }),
    )
    render(<PlanApprovalActions plan={PLAN} />)
    const retryBtn = await screen.findByRole('button', {
      name: /retry loading approval/i,
    })
    await userEvent.click(retryBtn)
    expect(
      await screen.findByRole('button', { name: /approve plan/i }),
    ).toBeInTheDocument()
  })

  it('renders nothing when no pending plan-review approval matches the plan', async () => {
    // The container is empty on the initial render, so wait for the lookup to
    // actually resolve before asserting empty -- otherwise the assertion could
    // pass before the GET returns and never exercise the empty response.
    const listSpy = vi.fn()
    server.use(
      http.get('/api/v1/approvals', () => {
        listSpy()
        return HttpResponse.json(paginatedFor<typeof listApprovals>(emptyPage()))
      }),
    )
    const { container } = render(<PlanApprovalActions plan={PLAN} />)
    await waitFor(() => expect(listSpy).toHaveBeenCalled())
    expect(container).toBeEmptyDOMElement()
  })
})

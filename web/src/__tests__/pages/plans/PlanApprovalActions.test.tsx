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

  it('renders nothing when no pending plan-review approval matches the plan', async () => {
    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(paginatedFor<typeof listApprovals>(emptyPage())),
      ),
    )
    const { container } = render(<PlanApprovalActions plan={PLAN} />)
    await waitFor(() => {
      expect(container).toBeEmptyDOMElement()
    })
  })
})

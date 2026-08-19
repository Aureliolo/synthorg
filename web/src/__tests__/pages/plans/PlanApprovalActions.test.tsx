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
    action_type: 'plan:approve',
    metadata: { plan_id: 'plan-1' },
  })
}

// The gate parks one of these per unresolved plan question, under the SAME
// source and the SAME plan_id as the plan approval itself.
function planQuestionApproval(id: string) {
  return makeApproval(id, {
    source: 'plan_review',
    status: 'pending',
    action_type: 'clarify:question',
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

  it('approves the plan, never a question parked on the same plan', async () => {
    // A live run clicked "Approve plan" and settled a parked clarify question:
    // the lookup matched on plan_id alone, and the questions carry the same
    // source and the same plan_id, so whichever the API ordered first won. The
    // plan stayed pending and the audit recorded the operator as having
    // decided a question they were never shown.
    const approved: string[] = []
    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(
          paginatedFor<typeof listApprovals>({
            ...emptyPage(),
            // Questions first, exactly as the live API ordered them.
            data: [
              planQuestionApproval('question-1'),
              planQuestionApproval('question-2'),
              planReviewApproval(),
            ],
          }),
        ),
      ),
      http.post('/api/v1/approvals/:id/approve', ({ params }) => {
        approved.push(String(params['id']))
        return HttpResponse.json(
          successFor<() => Promise<unknown>>(planReviewApproval()),
        )
      }),
    )
    render(<PlanApprovalActions plan={PLAN} />)
    await userEvent.click(await screen.findByRole('button', { name: /approve plan/i }))
    await waitFor(() => {
      expect(approved).toEqual(['appr-1'])
    })
  })

  it('offers no approve control when only questions are parked', async () => {
    // Better to show nothing than a control wired to the wrong decision.
    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(
          paginatedFor<typeof listApprovals>({
            ...emptyPage(),
            data: [planQuestionApproval('question-1')],
          }),
        ),
      ),
    )
    render(<PlanApprovalActions plan={PLAN} />)
    await waitFor(() => {
      expect(screen.queryByRole('button', { name: /approve plan/i })).toBeNull()
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

  it('still offers the decision once the plan has left review with the approval parked', async () => {
    // A plan can leave ``pending_review`` with its approval still pending (a
    // resume, a supersede). Gating the controls on the status left the one
    // decision the feature exists to take reachable from nowhere, while the
    // backend logged ``timeout.waiting`` against it once a minute forever.
    server.use(
      http.get('/api/v1/approvals', () =>
        HttpResponse.json(
          paginatedFor<typeof listApprovals>({
            ...emptyPage(),
            data: [planReviewApproval()],
          }),
        ),
      ),
    )
    render(<PlanApprovalActions plan={makePlan('plan-1', { status: 'executing' })} />)
    expect(
      await screen.findByRole('button', { name: /approve plan/i }),
    ).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /reject/i })).toBeInTheDocument()
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

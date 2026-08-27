import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it } from 'vitest'
import { PendingApprovalsCard } from '@/pages/dashboard/PendingApprovalsCard'
import {
  selectInboxApprovals,
  selectPendingInboxCount,
  selectPendingPlanReviewCount,
} from '@/stores/approvals/selectors'
import type { ApprovalResponse } from '@/api/types/approvals'

function renderCard(props: {
  count: number
  planReviewCount?: number
  loading?: boolean
}) {
  return render(
    <MemoryRouter>
      <PendingApprovalsCard {...props} />
    </MemoryRouter>,
  )
}

function makeApproval(
  id: string,
  overrides: Partial<ApprovalResponse> = {},
): ApprovalResponse {
  return {
    id,
    action_type: 'code:write',
    title: 'Do a thing',
    description: 'A thing to do',
    requested_by: 'agent-1',
    risk_level: 'medium',
    status: 'pending',
    source: 'review_gate',
    created_at: '2026-08-26T10:00:00Z',
    decided_at: null,
    decided_by: null,
    decision_reason: null,
    metadata: {},
    ...overrides,
  } as ApprovalResponse
}

describe('PendingApprovalsCard', () => {
  it('sends a plan review to the page that can decide it', () => {
    // The whole defect in one assertion: the card counted a plan review and
    // linked to the Approvals inbox, which excludes them, so the operator was
    // promised a decision on a page that showed none.
    renderCard({ count: 0, planReviewCount: 1 })

    const link = screen.getByRole('link', { name: /1 plan awaits your decision/ })
    expect(link).toHaveAttribute('href', '/plans')
  })

  it('sends an inbox approval to the inbox', () => {
    renderCard({ count: 2, planReviewCount: 0 })

    const link = screen.getByRole('link', { name: /2 items await your decision/ })
    expect(link).toHaveAttribute('href', '/approvals')
  })

  it('shows both destinations rather than one summed link', () => {
    renderCard({ count: 3, planReviewCount: 1 })

    expect(screen.getByRole('link', { name: /3 items/ })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /1 plan/ })).toBeInTheDocument()
    // A single "4" under one link is what cannot be routed correctly.
    expect(screen.queryByText('4')).not.toBeInTheDocument()
  })

  it('is empty only when neither destination has anything', () => {
    renderCard({ count: 0, planReviewCount: 0 })

    expect(screen.getByText('No approvals waiting')).toBeInTheDocument()
  })

  it('does not flash the empty state while the shared fetch is in flight', () => {
    renderCard({ count: 0, planReviewCount: 0, loading: true })

    expect(screen.queryByText('No approvals waiting')).not.toBeInTheDocument()
  })
})

describe('approvals selectors', () => {
  const rows = [
    makeApproval('a'),
    makeApproval('b', { status: 'approved' }),
    makeApproval('p1', {
      source: 'plan_review',
      metadata: { plan_id: 'plan-1' },
    }),
    makeApproval('p2', {
      source: 'plan_review',
      metadata: { plan_id: 'plan-1' },
    }),
    makeApproval('p3', {
      source: 'plan_review',
      status: 'approved',
      metadata: { plan_id: 'plan-2' },
    }),
  ]

  it('keeps plan reviews out of the inbox list', () => {
    expect(selectInboxApprovals(rows).map((a) => a.id)).toEqual(['a', 'b'])
  })

  it('counts only pending inbox rows', () => {
    expect(selectPendingInboxCount(rows)).toBe(1)
  })

  it('counts plans, not the rows one plan parks', () => {
    // One plan under review parks an approval plus a row per open question;
    // counting rows puts a 2 beside a link to a single plan.
    expect(selectPendingPlanReviewCount(rows)).toBe(1)
  })

  it('counts an unattributed plan review as itself', () => {
    const unattributed = [
      makeApproval('x', { source: 'plan_review' }),
      makeApproval('y', { source: 'plan_review' }),
    ]

    expect(selectPendingPlanReviewCount(unattributed)).toBe(2)
  })

  it('the two counts partition the pending rows between them', () => {
    // Neither surface may hide a decision the other does not show.
    const pending = rows.filter((a) => a.status === 'pending')
    const planRows = pending.filter((a) => a.source === 'plan_review')
    expect(selectPendingInboxCount(rows) + planRows.length).toBe(pending.length)
  })
})

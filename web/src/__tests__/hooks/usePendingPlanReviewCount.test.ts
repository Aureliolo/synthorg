import { renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it } from 'vitest'

import { usePendingPlanReviewCount } from '@/hooks/usePendingPlanReviewCount'
import { useApprovalsStore } from '@/stores/approvals'

import { makeApproval } from '../helpers/factories'

beforeEach(() => {
  useApprovalsStore.setState({ approvals: [] })
})

describe('usePendingPlanReviewCount', () => {
  it('counts only pending plan-review approvals', () => {
    useApprovalsStore.setState({
      approvals: [
        makeApproval('p1', { source: 'plan_review', status: 'pending' }),
        makeApproval('p2', { source: 'plan_review', status: 'pending' }),
        // Decided plan review: excluded.
        makeApproval('p3', { source: 'plan_review', status: 'approved' }),
        // A different source: excluded (it belongs to the generic inbox).
        makeApproval('r1', { source: 'review_gate', status: 'pending' }),
      ],
    })
    const { result } = renderHook(() => usePendingPlanReviewCount())
    expect(result.current.pendingCount).toBe(2)
  })

  it('is zero when there are no pending plan reviews', () => {
    useApprovalsStore.setState({
      approvals: [makeApproval('r1', { source: 'review_gate', status: 'pending' })],
    })
    const { result } = renderHook(() => usePendingPlanReviewCount())
    expect(result.current.pendingCount).toBe(0)
  })
})

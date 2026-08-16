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
        makeApproval('p1', {
          source: 'plan_review',
          status: 'pending',
          metadata: { plan_id: 'plan-1' },
        }),
        makeApproval('p2', {
          source: 'plan_review',
          status: 'pending',
          metadata: { plan_id: 'plan-2' },
        }),
        // Decided plan review: excluded.
        makeApproval('p3', { source: 'plan_review', status: 'approved' }),
        // A different source: excluded (it belongs to the generic inbox).
        makeApproval('r1', { source: 'review_gate', status: 'pending' }),
      ],
    })
    const { result } = renderHook(() => usePendingPlanReviewCount())
    expect(result.current.pendingCount).toBe(2)
  })

  it('counts one plan once however many rows it has parked', () => {
    // A plan under review parks its approval plus one row per open question,
    // so a per-row count put a red 3 beside a link to a single plan.
    useApprovalsStore.setState({
      approvals: [
        makeApproval('appr', {
          source: 'plan_review',
          status: 'pending',
          metadata: { plan_id: 'plan-1' },
        }),
        makeApproval('q1', {
          source: 'plan_review',
          status: 'pending',
          metadata: { plan_id: 'plan-1' },
        }),
        makeApproval('q2', {
          source: 'plan_review',
          status: 'pending',
          metadata: { plan_id: 'plan-1' },
        }),
      ],
    })
    const { result } = renderHook(() => usePendingPlanReviewCount())
    expect(result.current.pendingCount).toBe(1)
  })

  it('counts a row naming no plan as itself', () => {
    // Still one decision to take. Collapsing every unattributed row under one
    // key would report several as one.
    useApprovalsStore.setState({
      approvals: [
        makeApproval('a', { source: 'plan_review', status: 'pending' }),
        makeApproval('b', { source: 'plan_review', status: 'pending' }),
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

import { useApprovalsStore } from '@/stores/approvals'

export interface UsePendingPlanReviewCountReturn {
  pendingCount: number
}

/**
 * Track the number of PENDING plan-review approvals.
 *
 * A plan review is decision-gathering, not a binary approval, so it lives on
 * the Plan Review page rather than the generic Approvals inbox. This derives
 * its count from the same approvals store the sidebar's approvals badge already
 * keeps live (poll + WebSocket), so no second fetch is issued: the
 * always-mounted {@link usePendingApprovalsCount} owns the one request.
 */
export function usePendingPlanReviewCount(): UsePendingPlanReviewCountReturn {
  const pendingCount = useApprovalsStore(
    (s) =>
      s.approvals.filter(
        (a) => a.status === 'pending' && a.source === 'plan_review',
      ).length,
  )
  return { pendingCount }
}

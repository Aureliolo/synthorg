import { useApprovalsStore } from '@/stores/approvals'

export interface UsePendingPlanReviewCountReturn {
  pendingCount: number
}

/**
 * Track the number of PLANS awaiting the operator's decision.
 *
 * A plan review is decision-gathering, not a binary approval, so it lives on
 * the Plan Review page rather than the generic Approvals inbox. This derives
 * its count from the same approvals store the sidebar's approvals badge already
 * keeps live (poll + WebSocket), so no second fetch is issued: the
 * always-mounted {@link usePendingApprovalsCount} owns the one request.
 *
 * Counted per PLAN, not per approval row. One plan under review parks an
 * approval plus one row per open question, so counting rows put a red 3 beside
 * a link to a single plan, and an operator had no way to reconcile the two
 * numbers from the dashboard. A count beside a nav item reads as "this many
 * things need you", and the thing that needs them is the plan.
 */
export function usePendingPlanReviewCount(): UsePendingPlanReviewCountReturn {
  const pendingCount = useApprovalsStore(
    (s) =>
      new Set(
        s.approvals
          .filter((a) => a.status === 'pending' && a.source === 'plan_review')
          // An approval that names no plan is still one decision to take, so
          // it counts as itself rather than collapsing with every other
          // unattributed row under one key.
          .map((a) => a.metadata['plan_id'] ?? a.id),
      ).size,
  )
  return { pendingCount }
}

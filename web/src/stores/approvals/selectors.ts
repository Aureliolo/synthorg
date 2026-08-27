import type { ApprovalResponse } from '@/api/types/approvals'

/**
 * A plan review is decision-gathering rather than a binary approval, so it is
 * taken on the Plan Review page and not in the generic inbox. That split is
 * deliberate, and it makes "how many decisions await me" two questions with
 * two destinations.
 *
 * The two answers live here because three surfaces ask them and one of the
 * three asked without the split: the dashboard card counted every pending row,
 * found the plan review the inbox excludes, and linked the operator to a page
 * that rendered nothing. The card said one item awaited a decision and the
 * page it pointed at said zero.
 *
 * Written over the row list rather than over the store, so the inbox can
 * memoise on the array it already holds and the store surfaces compose them in
 * a selector.
 */
function isPending(approval: ApprovalResponse): boolean {
  return approval.status === 'pending'
}

function isPlanReview(approval: ApprovalResponse): boolean {
  return approval.source === 'plan_review'
}

/** Rows the generic Approvals inbox lists, in the order given. */
export function selectInboxApprovals(
  approvals: readonly ApprovalResponse[],
): ApprovalResponse[] {
  return approvals.filter((a) => !isPlanReview(a))
}

/** Pending decisions the operator takes in the Approvals inbox. */
export function selectPendingInboxCount(
  approvals: readonly ApprovalResponse[],
): number {
  return approvals.filter((a) => isPending(a) && !isPlanReview(a)).length
}

/**
 * Plans awaiting the operator's decision.
 *
 * Counted per PLAN, not per approval row: one plan under review parks an
 * approval plus one row per open question, so counting rows put a red 3 beside
 * a link to a single plan and an operator had no way to reconcile the two
 * numbers. A count beside a link reads as "this many things need you", and the
 * thing that needs them is the plan.
 */
export function selectPendingPlanReviewCount(
  approvals: readonly ApprovalResponse[],
): number {
  return new Set(
    approvals
      .filter((a) => isPending(a) && isPlanReview(a))
      // An approval that names no plan is still one decision to take, so it
      // counts as itself rather than collapsing with every other unattributed
      // row under one key.
      .map((a) => a.metadata['plan_id'] ?? a.id),
  ).size
}

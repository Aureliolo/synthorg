/**
 * What a plan's status means for the surfaces that ask something of the reader.
 *
 * Kept apart from the item-level derivations in `plans.ts`: those answer
 * questions about the items a plan contains, and this answers whether the plan
 * is still in a state where the answer matters.
 */

import type { PlanStatus } from '@/api/types/plans'

/**
 * The statuses where the operator's review decision is still open, which are
 * exactly the ones the review workspace offers controls for.
 */
const AWAITING_REVIEW_STATUSES: ReadonlySet<PlanStatus> = new Set<PlanStatus>([
  'draft',
  'pending_review',
])

/**
 * Whether reviewing this plan's items could still change anything.
 *
 * One predicate answers both "does this surface ask for a review" and "can one
 * be given", because they were decided separately and disagreed: a superseded
 * plan sat in the review inbox advertising items to review on a revision the
 * org had already replaced, and an executing plan headlined six reviews and a
 * panel asking for input while offering no control that could act on either.
 * The items still carry their flags, because a flag is a property of the item,
 * which is why the question has to be asked of the plan instead.
 */
export function planSolicitsReview(status: PlanStatus): boolean {
  return AWAITING_REVIEW_STATUSES.has(status)
}

/**
 * The statuses where the org is working the plan: approved and waiting for the
 * skeleton, writing the contract, running a wave, assembling, or scoring the
 * result.
 */
const RUNNING_STATUSES: ReadonlySet<PlanStatus> = new Set<PlanStatus>([
  'approved',
  'skeleton',
  'executing',
  'integrating',
  'evaluating',
])

/** Whether there is a run behind this plan for the operator to watch. */
export function planIsRunning(status: PlanStatus): boolean {
  return RUNNING_STATUSES.has(status)
}

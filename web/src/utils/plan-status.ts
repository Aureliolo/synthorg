/**
 * What a plan's status means for the surfaces that ask something of the reader.
 *
 * Kept apart from the item-level derivations in `plans.ts`: those answer
 * questions about the items a plan contains, and this answers whether the plan
 * is still in a state where the answer matters.
 */

import type { PlanStatus } from '@/api/types/plans'

/**
 * The statuses with no lifecycle hops left, mirroring `TERMINAL_STATUSES`
 * server-side. The plan has been delivered, declined, replaced, or failed to
 * decompose.
 */
const TERMINAL_STATUSES: ReadonlySet<PlanStatus> = new Set<PlanStatus>([
  'completed',
  'rejected',
  'superseded',
  'failed',
])

/**
 * Whether reviewing this plan's items could still change anything.
 *
 * A superseded plan sat in the review inbox advertising items to review, on a
 * revision the org had already replaced: the operator is asked to weigh in on a
 * decision that has been taken and cannot be retaken here. The items still
 * carry their flags, because a flag is a property of the item, which is why the
 * question has to be asked of the plan instead.
 */
export function planSolicitsReview(status: PlanStatus): boolean {
  return !TERMINAL_STATUSES.has(status)
}

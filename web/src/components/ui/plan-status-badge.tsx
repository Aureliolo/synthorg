import type { PlanStatus } from '@/api/types/plans'

import { StatusPill, type StatusPillTone } from './status-pill'

const STATUS_LABELS: Record<PlanStatus, string> = {
  planning: 'Planning',
  draft: 'Draft',
  pending_review: 'Pending review',
  approved: 'Approved',
  skeleton: 'Writing the contract',
  executing: 'Executing',
  integrating: 'Integrating',
  evaluating: 'Evaluating',
  completed: 'Completed',
  rejected: 'Rejected',
  superseded: 'Superseded',
  failed: 'Failed',
}

const STATUS_TONES: Record<PlanStatus, StatusPillTone> = {
  planning: 'text-secondary',
  draft: 'text-secondary',
  pending_review: 'warning',
  approved: 'success',
  skeleton: 'accent',
  executing: 'accent',
  integrating: 'accent',
  evaluating: 'accent',
  completed: 'success',
  rejected: 'danger',
  superseded: 'text-secondary',
  failed: 'danger',
}

export interface PlanStatusBadgeProps {
  status: PlanStatus
  className?: string
}

/**
 * Inline status pill for a plan's lifecycle state, from draft through the
 * tail: the contract is written first, then executing, then integrating, then
 * evaluating, then completed.
 *
 * The skeleton reads as "Writing the contract" rather than as its status name,
 * because the operator's question at that point is what the org is doing, and
 * "Skeleton" answers a different one.
 */
export function PlanStatusBadge({ status, className }: PlanStatusBadgeProps) {
  return (
    <StatusPill tone={STATUS_TONES[status]} className={className}>
      {STATUS_LABELS[status]}
    </StatusPill>
  )
}

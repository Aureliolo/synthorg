import type { PlanStatus } from '@/api/types/plans'

import { StatusPill, type StatusPillTone } from './status-pill'

const STATUS_LABELS: Record<PlanStatus, string> = {
  planning: 'Planning',
  draft: 'Draft',
  pending_review: 'Pending review',
  approved: 'Approved',
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
 * tail: executing, then integrating, then evaluating, then completed.
 */
export function PlanStatusBadge({ status, className }: PlanStatusBadgeProps) {
  return (
    <StatusPill tone={STATUS_TONES[status]} className={className}>
      {STATUS_LABELS[status]}
    </StatusPill>
  )
}

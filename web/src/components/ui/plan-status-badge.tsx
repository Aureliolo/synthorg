import type { PlanStatus } from '@/api/types'

import { StatusPill, type StatusPillTone } from './status-pill'

const STATUS_LABELS: Record<PlanStatus, string> = {
  draft: 'Draft',
  pending_review: 'Pending review',
  approved: 'Approved',
  rejected: 'Rejected',
  superseded: 'Superseded',
}

const STATUS_TONES: Record<PlanStatus, StatusPillTone> = {
  draft: 'text-secondary',
  pending_review: 'warning',
  approved: 'success',
  rejected: 'danger',
  superseded: 'text-secondary',
}

export interface PlanStatusBadgeProps {
  status: PlanStatus
  className?: string
}

/** Inline status pill for a plan's lifecycle state (draft → approved/rejected). */
export function PlanStatusBadge({ status, className }: PlanStatusBadgeProps) {
  return (
    <StatusPill tone={STATUS_TONES[status]} className={className}>
      {STATUS_LABELS[status]}
    </StatusPill>
  )
}

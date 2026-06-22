import { getMeetingStatusColor, getMeetingStatusLabel, STATUS_BADGE_CLASSES } from '@/utils/meetings'
import type { MeetingStatus } from '@/api/types/meetings'
import { StatusPill } from './status-pill'

export interface MeetingStatusBadgeProps {
  status: MeetingStatus
  className?: string
}

/**
 * Coloured status pill for meetings, shared across the meeting card and
 * detail header so the colour/label mapping lives in one place rather than
 * being re-derived inline at every call site. Shares the canonical pill shape
 * via {@link StatusPill}, with the meeting-specific palette via `toneClassName`.
 */
export function MeetingStatusBadge({ status, className }: MeetingStatusBadgeProps) {
  return (
    <StatusPill
      toneClassName={STATUS_BADGE_CLASSES[getMeetingStatusColor(status)]}
      className={className}
    >
      {getMeetingStatusLabel(status)}
    </StatusPill>
  )
}

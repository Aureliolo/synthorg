import { cn } from '@/lib/utils'
import { getMeetingStatusColor, getMeetingStatusLabel, STATUS_BADGE_CLASSES } from '@/utils/meetings'
import type { MeetingStatus } from '@/api/types/meetings'

export interface MeetingStatusBadgeProps {
  status: MeetingStatus
  className?: string
}

/**
 * Coloured status pill for meetings, shared across the meeting card and
 * detail header so the colour/label mapping lives in one place rather than
 * being re-derived inline at every call site.
 */
export function MeetingStatusBadge({ status, className }: MeetingStatusBadgeProps) {
  return (
    <span
      className={cn(
        'shrink-0 rounded-full border px-2 py-0.5 text-micro font-medium',
        STATUS_BADGE_CLASSES[getMeetingStatusColor(status)],
        className,
      )}
    >
      {getMeetingStatusLabel(status)}
    </span>
  )
}

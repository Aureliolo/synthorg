import { Link } from 'react-router'
import { cn } from '@/lib/utils'
import { formatLabel, formatRelativeTime } from '@/utils/format'
import { getMeetingStatusColor, STATUS_DOT_CLASSES } from '@/utils/meetings'
import { ROUTES } from '@/router/routes'
import type { MeetingResponse } from '@/api/types/meetings'

interface MeetingTimelineNodeProps {
  meeting: MeetingResponse
  className?: string
}

export function MeetingTimelineNode({ meeting, className }: MeetingTimelineNodeProps) {
  const color = getMeetingStatusColor(meeting.status)
  const dotClass = STATUS_DOT_CLASSES[color]
  const participantCount = meeting.minutes?.participant_ids.length ?? 0
  const startedAt = meeting.minutes?.started_at ?? null
  const isActive = meeting.status === 'in_progress'

  return (
    <Link
      to={ROUTES.MEETING_DETAIL.replace(':meetingId', meeting.meeting_id)}
      className={cn(
        'group flex shrink-0 flex-col items-center gap-1.5 rounded-lg border border-border bg-card p-card',
        'transition-colors duration-200 hover:bg-card-hover hover:border-bright',
        'w-36 snap-start',
        className,
      )}
      aria-label={`${formatLabel(meeting.meeting_type_name)} meeting, ${meeting.status}`}
    >
      <div className="flex items-center gap-2">
        <span
          className={cn(
            'size-2 shrink-0 rounded-full',
            dotClass,
            isActive && 'animate-pulse',
          )}
          aria-hidden="true"
        />
        <span className="truncate text-xs font-medium text-foreground">
          {formatLabel(meeting.meeting_type_name)}
        </span>
      </div>
      <div className="flex items-center gap-2 text-micro text-muted-foreground">
        <span>{participantCount} agents</span>
      </div>
      {startedAt && (
        <time
          dateTime={startedAt}
          className="text-micro font-mono text-muted-foreground"
        >
          {formatRelativeTime(startedAt)}
        </time>
      )}
    </Link>
  )
}

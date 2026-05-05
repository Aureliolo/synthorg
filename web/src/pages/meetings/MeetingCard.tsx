import { memo } from 'react'
import { Link } from 'react-router'
import { Clock, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatLabel, formatRelativeTime, formatTokenCount } from '@/utils/format'
import {
  formatMeetingDuration,
  getMeetingStatusColor,
  getMeetingStatusLabel,
  getProtocolLabel,
  computeTokenUsagePercent,
  STATUS_BADGE_CLASSES,
} from '@/utils/meetings'
import { ROUTES } from '@/router/routes'
import type { MeetingResponse } from '@/api/types/meetings'

interface MeetingCardProps {
  meeting: MeetingResponse
  className?: string
}

function MeetingCardImpl({ meeting, className }: MeetingCardProps) {
  const statusColor = getMeetingStatusColor(meeting.status)
  const badgeClass = STATUS_BADGE_CLASSES[statusColor]
  const participantCount = meeting.minutes?.participant_ids.length ?? 0
  const startedAt = meeting.minutes?.started_at ?? null
  const tokenPercent = computeTokenUsagePercent(meeting)

  return (
    <Link
      to={ROUTES.MEETING_DETAIL.replace(':meetingId', meeting.meeting_id)}
      className={cn(
        'flex flex-col gap-3 rounded-lg border border-border bg-card p-card',
        'transition-all duration-200 hover:bg-card-hover hover:-translate-y-px',
        'hover:shadow-[var(--so-shadow-card-hover)]',
        className,
      )}
    >
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          <span className="truncate text-sm font-semibold text-foreground">
            {formatLabel(meeting.meeting_type_name)}
          </span>
          <span className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-mono text-muted-foreground">
            {getProtocolLabel(meeting.protocol_type)}
          </span>
        </div>
        <span
          className={cn(
            'shrink-0 rounded-full border px-2 py-0.5 text-micro font-medium',
            badgeClass,
          )}
        >
          {getMeetingStatusLabel(meeting.status)}
        </span>
      </div>

      {/* Stats row */}
      <div className="flex items-center gap-4 text-xs text-muted-foreground">
        <span className="flex items-center gap-1">
          <Users className="size-3.5" aria-hidden="true" />
          {participantCount}
        </span>
        <span className="flex items-center gap-1">
          <Clock className="size-3.5" aria-hidden="true" />
          {formatMeetingDuration(meeting.meeting_duration_seconds)}
        </span>
        {meeting.minutes && (
          <span className="font-mono">
            {formatTokenCount(meeting.minutes.total_tokens)} tokens
          </span>
        )}
      </div>

      {/* Token usage mini-bar */}
      {meeting.token_budget > 0 && meeting.minutes && (
        <div className="h-1 w-full overflow-hidden rounded-full bg-border">
          <div
            className={cn(
              'h-full rounded-full transition-all duration-[900ms]',
              tokenPercent > 90 ? 'bg-danger' : tokenPercent > 70 ? 'bg-warning' : 'bg-accent',
            )}
            style={{
              width: `${tokenPercent}%`,
              transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
            }}
          />
        </div>
      )}

      {/* Timestamp */}
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

export const MeetingCard = memo(MeetingCardImpl)

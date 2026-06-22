import { memo } from 'react'
import { Link } from 'react-router'
import { Clock, Users } from 'lucide-react'
import { cn } from '@/lib/utils'
import { formatDateTime, formatLabel, formatRelativeTime, formatTokenCount } from '@/utils/format'
import {
  formatMeetingDuration,
  getProtocolLabel,
  computeTokenUsagePercent,
} from '@/utils/meetings'
import { MeetingStatusBadge } from '@/components/ui/meeting-status-badge'
import { ROUTES } from '@/router/routes'
import type { MeetingResponse } from '@/api/types/meetings'

interface MeetingCardProps {
  meeting: MeetingResponse
  className?: string
}

const TOKEN_BAR_HIGH = 90
const TOKEN_BAR_MEDIUM = 70

function tokenBarColorClass(percent: number): string {
  if (percent > TOKEN_BAR_HIGH) return 'bg-danger'
  if (percent > TOKEN_BAR_MEDIUM) return 'bg-warning'
  return 'bg-accent'
}

function MeetingCardHeader({ meeting }: { meeting: MeetingResponse }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-2 min-w-0">
        <span className="truncate text-sm font-semibold text-foreground">
          {formatLabel(meeting.meeting_type_name)}
        </span>
        <span className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-mono text-muted-foreground">
          {getProtocolLabel(meeting.protocol_type)}
        </span>
      </div>
      <MeetingStatusBadge status={meeting.status} />
    </div>
  )
}

function MeetingCardStats({ meeting }: { meeting: MeetingResponse }) {
  return (
    <div className="flex items-center gap-4 text-xs text-muted-foreground">
      <span className="flex items-center gap-1">
        <Users className="size-3.5" aria-hidden="true" />
        {meeting.minutes?.participant_ids.length ?? 0}
      </span>
      <span className="flex items-center gap-1">
        <Clock className="size-3.5" aria-hidden="true" />
        {formatMeetingDuration(meeting.meeting_duration_seconds ?? null)}
      </span>
      {meeting.minutes && (
        <span className="font-mono">
          {formatTokenCount(meeting.minutes.total_tokens)} tokens
        </span>
      )}
    </div>
  )
}

function MeetingTokenBar({ meeting }: { meeting: MeetingResponse }) {
  if (meeting.token_budget <= 0 || !meeting.minutes) return null
  const tokenPercent = computeTokenUsagePercent(meeting)
  return (
    <div className="h-1 w-full overflow-hidden rounded-full bg-border">
      <div
        className={cn(
          'h-full rounded-full transition-all duration-[var(--so-transition-progress)]',
          tokenBarColorClass(tokenPercent),
        )}
        style={{
          width: `${tokenPercent}%`,
          transitionTimingFunction: 'cubic-bezier(0.4, 0, 0.2, 1)',
        }}
      />
    </div>
  )
}

function MeetingCardImpl({ meeting, className }: MeetingCardProps) {
  const startedAt = meeting.minutes?.started_at ?? null

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
      <MeetingCardHeader meeting={meeting} />
      <MeetingCardStats meeting={meeting} />
      <MeetingTokenBar meeting={meeting} />

      {startedAt && (
        <time
          dateTime={startedAt}
          title={formatDateTime(startedAt)}
          className="text-micro font-mono text-muted-foreground"
        >
          {formatRelativeTime(startedAt)}
        </time>
      )}
    </Link>
  )
}

export const MeetingCard = memo(MeetingCardImpl)

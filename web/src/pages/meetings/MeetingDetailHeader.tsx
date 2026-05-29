import { Link } from 'react-router'
import { ArrowLeft, Clock, Hash } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { StatPill } from '@/components/ui/stat-pill'
import { formatDateTime, formatLabel, formatTokenCount } from '@/utils/format'
import {
  formatMeetingDuration,
  getMeetingStatusColor,
  getMeetingStatusLabel,
  getProtocolLabel,
  STATUS_BADGE_CLASSES,
} from '@/utils/meetings'
import { ROUTES } from '@/router/routes'
import type { MeetingResponse } from '@/api/types/meetings'

interface MeetingDetailHeaderProps {
  meeting: MeetingResponse
  className?: string
}

function MeetingHeaderTitle({ meeting }: { meeting: MeetingResponse }) {
  const statusBadgeClass = STATUS_BADGE_CLASSES[getMeetingStatusColor(meeting.status)]
  return (
    <div className="flex items-center gap-3">
      <Button asChild variant="ghost" size="icon-sm">
        <Link to={ROUTES.MEETINGS} aria-label="Back to meetings">
          <ArrowLeft className="size-4" />
        </Link>
      </Button>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <h1 className="truncate text-lg font-semibold text-foreground">
            {formatLabel(meeting.meeting_type_name)}
          </h1>
          <span className="shrink-0 rounded border border-border bg-surface px-1.5 py-0.5 text-micro font-mono text-muted-foreground">
            {getProtocolLabel(meeting.protocol_type)}
          </span>
          <span
            className={cn(
              'shrink-0 rounded-full border px-2 py-0.5 text-micro font-medium',
              statusBadgeClass,
            )}
          >
            {getMeetingStatusLabel(meeting.status)}
          </span>
        </div>
      </div>
    </div>
  )
}

function MeetingHeaderStats({ meeting }: { meeting: MeetingResponse }) {
  const participantCount = meeting.minutes?.participant_ids.length ?? 0
  return (
    <div className="flex flex-wrap items-center gap-3">
      <StatPill
        label="Duration"
        value={formatMeetingDuration(meeting.meeting_duration_seconds ?? null)}
      />
      <StatPill label="Participants" value={participantCount} />
      {meeting.minutes && (
        <StatPill label="Tokens" value={formatTokenCount(meeting.minutes.total_tokens)} />
      )}
      <StatPill label="Budget" value={formatTokenCount(meeting.token_budget)} />
    </div>
  )
}

function MeetingHeaderTimestamps({ meeting }: { meeting: MeetingResponse }) {
  const startedAt = meeting.minutes?.started_at ?? null
  const endedAt = meeting.minutes?.ended_at ?? null
  return (
    <div className="flex flex-wrap gap-4 text-xs text-muted-foreground">
      {startedAt && (
        <span className="flex items-center gap-1">
          <Clock className="size-3.5" aria-hidden="true" />
          Started: {formatDateTime(startedAt)}
        </span>
      )}
      {endedAt && (
        <span className="flex items-center gap-1">
          <Clock className="size-3.5" aria-hidden="true" />
          Ended: {formatDateTime(endedAt)}
        </span>
      )}
      <span className="flex items-center gap-1">
        <Hash className="size-3.5" aria-hidden="true" />
        {meeting.meeting_id}
      </span>
    </div>
  )
}

export function MeetingDetailHeader({ meeting, className }: MeetingDetailHeaderProps) {
  return (
    <div className={cn('space-y-4', className)}>
      <MeetingHeaderTitle meeting={meeting} />
      <MeetingHeaderStats meeting={meeting} />
      <MeetingHeaderTimestamps meeting={meeting} />
    </div>
  )
}

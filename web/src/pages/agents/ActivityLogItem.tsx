import { ActivityEventIcon } from '@/utils/activity-event-icon'
import { formatRelativeTime } from '@/utils/format'
import type { AgentActivityEvent } from '@/api/types/agents'

interface ActivityLogItemProps {
  event: AgentActivityEvent
}

export function ActivityLogItem({ event }: ActivityLogItemProps) {
  return (
    <div className="flex items-start gap-3 py-2">
      <ActivityEventIcon
        eventType={event.event_type}
        className="size-4 shrink-0 text-muted-foreground mt-0.5"
        aria-hidden="true"
      />
      <div className="flex-1 min-w-0">
        <p className="text-sm text-foreground">{event.description}</p>
      </div>
      <time
        dateTime={event.timestamp}
        className="text-micro font-mono text-muted-foreground shrink-0"
      >
        {formatRelativeTime(event.timestamp)}
      </time>
    </div>
  )
}

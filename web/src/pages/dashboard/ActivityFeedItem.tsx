import { memo } from 'react'
import { Link } from 'react-router'
import { Avatar } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'
import type { ActivityEventType } from '@/api/types/agents'
import type { ActivityItem } from '@/api/types/analytics'
import type { WsEventType } from '@/api/types/websocket'

interface ActivityFeedItemProps {
  activity: ActivityItem
  className?: string
}

/** Dot colors for both REST ActivityEventType and WS WsEventType keys. */
const ACTION_DOT_COLORS: Partial<Record<ActivityEventType | WsEventType, string>> = {
  // REST activity event types
  hired: 'bg-success',
  fired: 'bg-danger',
  onboarded: 'bg-success',
  offboarded: 'bg-warning',
  status_changed: 'bg-warning',
  promoted: 'bg-success',
  demoted: 'bg-warning',
  task_started: 'bg-accent',
  task_completed: 'bg-success',
  cost_incurred: 'bg-warning',
  tool_used: 'bg-accent',
  delegation_sent: 'bg-accent',
  delegation_received: 'bg-accent',
  // WS event types
  'task.created': 'bg-accent',
  'task.updated': 'bg-accent',
  'task.status_changed': 'bg-success',
  'task.assigned': 'bg-accent',
  'agent.hired': 'bg-success',
  'agent.fired': 'bg-danger',
  'agent.status_changed': 'bg-warning',
  'budget.record_added': 'bg-warning',
  'budget.alert': 'bg-danger',
  'approval.submitted': 'bg-accent',
  'approval.approved': 'bg-success',
  'approval.rejected': 'bg-danger',
  'approval.expired': 'bg-warning',
  'message.sent': 'bg-accent',
  'meeting.started': 'bg-accent',
  'meeting.completed': 'bg-success',
  'meeting.failed': 'bg-danger',
  'coordination.started': 'bg-accent',
  'coordination.phase_completed': 'bg-success',
  'coordination.completed': 'bg-success',
  'coordination.failed': 'bg-danger',
  'system.error': 'bg-danger',
  'system.startup': 'bg-success',
  'system.shutdown': 'bg-warning',
}

function getActionDotColor(actionType: ActivityEventType | WsEventType): string {
  return ACTION_DOT_COLORS[actionType] ?? 'bg-muted-foreground'
}

function ActivityFeedItemImpl({ activity, className }: ActivityFeedItemProps) {
  const dotColor = getActionDotColor(activity.action_type)

  return (
    <div
      className={cn(
        'flex items-start gap-3 rounded-md px-3 py-2',
        'transition-colors duration-150',
        className,
      )}
    >
      <div className="relative">
        <Avatar name={activity.agent_name} size="sm" />
        <span
          className={cn(
            'absolute -bottom-0.5 -right-0.5 size-[6px] rounded-full ring-1 ring-card',
            dotColor,
          )}
          aria-label={`Action: ${activity.action_type.replace(/[._]/g, ' ')}`}
        />
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-1.5">
          <span className="truncate text-sm font-semibold text-foreground">
            {activity.agent_name}
          </span>
          <span className="shrink-0 text-xs text-text-secondary">
            {activity.description}
          </span>
        </div>
        {activity.task_id && (
          <Link
            to={`/tasks/${activity.task_id}`}
            className="text-xs text-accent hover:underline"
          >
            {activity.task_id}
          </Link>
        )}
      </div>
      <span
        className="shrink-0 font-mono text-[10px] text-muted-foreground"
        data-testid="activity-timestamp"
      >
        {formatRelativeTime(activity.timestamp)}
      </span>
    </div>
  )
}

/**
 * Memoised: the activity feed updates on every WebSocket event, and
 * the parent re-renders the entire list each time. Without memo,
 * unchanged sibling rows pay the cost of re-rendering Avatar +
 * Link + formatRelativeTime on every WS update.
 */
export const ActivityFeedItem = memo(ActivityFeedItemImpl)

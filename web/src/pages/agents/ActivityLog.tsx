import { Activity } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { Button } from '@/components/ui/button'
import { ActivityLogItem } from './ActivityLogItem'
import type { AgentActivityEvent } from '@/api/types/agents'

interface ActivityLogProps {
  events: readonly AgentActivityEvent[]
  total: number
  onLoadMore: () => void
  className?: string
}

export function ActivityLog({ events, total, onLoadMore, className }: ActivityLogProps) {
  const hasMore = events.length < total

  return (
    <SectionCard
      title="Activity Log"
      icon={Activity}
      className={className}
      action={
        hasMore ? (
          <Button variant="ghost" size="sm" onClick={onLoadMore}>
            Load more
          </Button>
        ) : undefined
      }
    >
      {events.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No activity yet"
          description="Recent actions will appear here."
        />
      ) : (
        <StaggerGroup
          className="divide-y divide-border"
          role="list"
          aria-label="Agent activity log"
        >
          {events.map((event, index) => (
            <StaggerItem
              // eslint-disable-next-line @eslint-react/no-array-index-key -- events lack unique IDs; type+timestamp may collide for same-second events
              key={`${event.event_type}-${event.timestamp}-${index}`}
              role="listitem"
            >
              <ActivityLogItem event={event} />
            </StaggerItem>
          ))}
        </StaggerGroup>
      )}
    </SectionCard>
  )
}

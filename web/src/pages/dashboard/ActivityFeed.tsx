import { memo, useEffect, useMemo, useRef } from 'react'
import { Activity } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { StaggerGroup, StaggerItem } from '@/components/ui/stagger-group'
import { useAutoScroll } from '@/hooks/useAutoScroll'
import { ActivityFeedItem } from './ActivityFeedItem'
import type { ActivityItem } from '@/api/types/analytics'

const MAX_VISIBLE = 10

interface ActivityFeedProps {
  activities: readonly ActivityItem[]
}

function ActivityFeedInner({ activities }: ActivityFeedProps) {
  const visible = useMemo(() => activities.slice(0, MAX_VISIBLE), [activities])
  const feedRef = useRef<HTMLDivElement>(null)
  const { isAutoScrolling, scrollToBottom } = useAutoScroll(feedRef)
  const prevCountRef = useRef(activities.length)

  // Auto-scroll when new items arrive and user hasn't scrolled away
  // Track total activities.length (not visible.length which caps at MAX_VISIBLE)
  useEffect(() => {
    if (activities.length > prevCountRef.current && isAutoScrolling) {
      scrollToBottom()
    }
    prevCountRef.current = activities.length
  }, [activities.length, isAutoScrolling, scrollToBottom])

  return (
    <SectionCard title="Live Activity" icon={Activity}>
      {visible.length === 0 ? (
        <EmptyState
          icon={Activity}
          title="No activity yet"
          description="Agent actions will appear here in real time."
        />
      ) : (
        <div ref={feedRef} role="log" aria-live="polite" className="max-h-80 overflow-y-auto">
          <StaggerGroup className="divide-y divide-border">
            {visible.map((item) => (
              <StaggerItem key={item.id}>
                <ActivityFeedItem activity={item} />
              </StaggerItem>
            ))}
          </StaggerGroup>
        </div>
      )}
    </SectionCard>
  )
}

export const ActivityFeed = memo(ActivityFeedInner)

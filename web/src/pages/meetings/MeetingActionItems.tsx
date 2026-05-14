import { ClipboardList } from 'lucide-react'
import { Avatar } from '@/components/ui/avatar'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { PriorityBadge } from '@/components/ui/task-status-indicator'
import { formatLabel } from '@/utils/format'
import type { ActionItem } from '@/api/types/meetings'

interface MeetingActionItemsProps {
  actionItems: readonly ActionItem[]
  className?: string
}

export function MeetingActionItems({ actionItems, className }: MeetingActionItemsProps) {
  if (actionItems.length === 0) {
    return (
      <SectionCard title="Action Items" icon={ClipboardList} className={className}>
        <EmptyState
          icon={ClipboardList}
          title="No action items recorded"
          description="Action items extracted from this meeting will appear here."
        />
      </SectionCard>
    )
  }

  return (
    <SectionCard title="Action Items" icon={ClipboardList} className={className}>
      <ul className="space-y-3">
        {actionItems.map((item) => (
          <li key={`${item.assignee_id ?? 'unassigned'}-${item.description.slice(0, 40)}-${item.priority}`} className="flex items-start gap-3">
            {item.assignee_id ? (
              <Avatar name={item.assignee_id} size="sm" />
            ) : (
              <div className="flex size-6 items-center justify-center rounded-full bg-border text-micro text-muted-foreground">
                ?
              </div>
            )}
            <div className="min-w-0 flex-1">
              <p className="text-sm text-foreground">{item.description}</p>
              <div className="mt-1 flex items-center gap-2">
                <PriorityBadge priority={item.priority ?? 'medium'} />
                <span className="text-micro text-muted-foreground">
                  {item.assignee_id ? formatLabel(item.assignee_id) : 'Unassigned'}
                </span>
              </div>
            </div>
          </li>
        ))}
      </ul>
    </SectionCard>
  )
}

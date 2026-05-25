/** Single row inside a {@link VersionTimeline}: clickable version button. */

import { formatDateTime } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { TimelineItem } from './timeline-types'

export interface VersionHistoryItemProps<T extends TimelineItem> {
  item: T
  active: boolean
  onSelect: (item: T) => void
}

export function VersionHistoryItem<T extends TimelineItem>({
  item,
  active,
  onSelect,
}: VersionHistoryItemProps<T>) {
  return (
    <li>
      <button
        type="button"
        onClick={() => onSelect(item)}
        aria-current={active ? 'true' : undefined}
        className={cn(
          'flex w-full items-center justify-between gap-grid-gap px-card py-grid-gap text-left transition-colors',
          active ? 'bg-accent/10' : 'bg-card hover:bg-surface',
        )}
      >
        <span className="font-mono text-sm text-foreground">v{item.version}</span>
        <time
          dateTime={item.created_at}
          className="text-xs text-text-secondary"
        >
          {formatDateTime(item.created_at)}
        </time>
      </button>
    </li>
  )
}

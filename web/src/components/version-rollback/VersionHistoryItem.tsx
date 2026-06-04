/** Single row inside a {@link VersionTimeline}: clickable version button. */

import { formatDateTime } from '@/utils/format'
import { cn } from '@/lib/utils'
import type { TimelineItem } from './timeline-types'

export interface VersionHistoryItemProps<T extends TimelineItem> {
  item: T
  active: boolean
  onSelect: (item: T) => void
  /**
   * When false, the row is static text (no select interaction).
   * Used by surfaces with neither diff nor rollback (e.g. role
   * versions, whose backend exposes list + get only).
   */
  selectable?: boolean
}

function VersionItemContent({ item }: { item: TimelineItem }) {
  return (
    <>
      <span className="font-mono text-sm text-foreground">v{item.version}</span>
      <time dateTime={item.created_at} className="text-xs text-text-secondary">
        {formatDateTime(item.created_at)}
      </time>
    </>
  )
}

export function VersionHistoryItem<T extends TimelineItem>({
  item,
  active,
  onSelect,
  selectable = true,
}: VersionHistoryItemProps<T>) {
  if (!selectable) {
    return (
      <li className="flex w-full items-center justify-between gap-grid-gap px-card py-grid-gap text-left">
        <VersionItemContent item={item} />
      </li>
    )
  }
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
        <VersionItemContent item={item} />
      </button>
    </li>
  )
}

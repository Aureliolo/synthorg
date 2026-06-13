/**
 * Reusable read-only timeline of versioned snapshots.
 *
 * Domain-agnostic: takes a generic ``items`` array of
 * ``{ id, version, created_at }``-shaped records plus optional
 * load-more affordances.  Suitable for any backend that mirrors the
 * workflow-editor versioning pattern (agent identity, role, budget
 * config, evaluation config, company).
 */
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { VersionHistoryItem } from './VersionHistoryItem'
import type { TimelineItem } from './timeline-types'

export type { TimelineItem }

export interface VersionTimelineProps<T extends TimelineItem> {
  items: readonly T[]
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  selectedVersion: number | null
  onSelect: (item: T) => void
  onLoadMore: () => void
  emptyTitle?: string | undefined
  emptyDescription?: string | undefined
  /** When false, rows render as static text (no select / compare). */
  selectable?: boolean | undefined
}

export function VersionTimeline<T extends TimelineItem>({
  items,
  loading,
  loadingMore,
  hasMore,
  selectedVersion,
  onSelect,
  onLoadMore,
  emptyTitle = 'No versions',
  emptyDescription = 'Versioned changes will appear here.',
  // Defaulting is delegated to VersionHistoryItem (selectable=true) to
  // keep this function under the complexity cap; undefined => interactive.
  selectable,
}: VersionTimelineProps<T>) {
  if (loading && items.length === 0) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12 w-full" />
        ))}
      </div>
    )
  }

  if (items.length === 0) {
    return <EmptyState title={emptyTitle} description={emptyDescription} />
  }

  return (
    <div className="flex flex-col gap-grid-gap">
      <ol
        role="list"
        aria-label="Version history"
        className="flex flex-col divide-y divide-border rounded-md border border-border bg-card"
      >
        {items.map((item) => (
          <VersionHistoryItem
            key={item.id}
            item={item}
            active={selectedVersion === item.version}
            onSelect={onSelect}
            selectable={selectable}
          />
        ))}
      </ol>

      {hasMore && (
        <Button
          variant="secondary"
          onClick={onLoadMore}
          disabled={loadingMore}
        >
          {loadingMore ? 'Loading…' : 'Load more'}
        </Button>
      )}
    </div>
  )
}

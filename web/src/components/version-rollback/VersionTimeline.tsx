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
import { formatDateTime } from '@/utils/format'
import { cn } from '@/lib/utils'

export interface TimelineItem {
  readonly id: string
  readonly version: number
  readonly created_at: string
}

interface VersionTimelineProps<T extends TimelineItem> {
  items: readonly T[]
  loading: boolean
  loadingMore: boolean
  hasMore: boolean
  selectedVersion: number | null
  onSelect: (item: T) => void
  onLoadMore: () => void
  emptyTitle?: string
  emptyDescription?: string
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
        {items.map((item) => {
          const active = selectedVersion === item.version
          return (
            <li key={item.id}>
              <button
                type="button"
                onClick={() => onSelect(item)}
                aria-current={active ? 'true' : undefined}
                className={cn(
                  'flex w-full items-center justify-between gap-grid-gap px-card py-grid-gap text-left transition-colors',
                  active
                    ? 'bg-accent/10'
                    : 'bg-card hover:bg-surface',
                )}
              >
                <span className="font-mono text-sm text-foreground">
                  v{item.version}
                </span>
                <time className="text-xs text-text-secondary">
                  {formatDateTime(item.created_at)}
                </time>
              </button>
            </li>
          )
        })}
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

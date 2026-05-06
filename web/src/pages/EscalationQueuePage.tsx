/**
 * Escalation queue dashboard.
 *
 * Lists pending / decided / expired / cancelled escalations with
 * cursor-paginated fetch, surfaces a status filter, and opens a
 * detail drawer to view the underlying conflict and submit a
 * decision.
 */
import { useEffect, useMemo, useState } from 'react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Skeleton } from '@/components/ui/skeleton'
import { useEscalationsStore } from '@/stores/escalations'
import { useEmptyStateProps } from '@/hooks/use-empty-state-props'
import { formatDateTime } from '@/utils/format'
import { EscalationDetailDrawer } from './escalations/EscalationDetailDrawer'
import type { ConflictType, EscalationStatus } from '@/api/types/escalations'
import { cn } from '@/lib/utils'
import { ESCALATION_STATUS_BADGE_COLORS } from '@/styles/status-colors'

/**
 * Conflict-type buckets surfaced as the "priority" filter; the data
 * model has no explicit priority field, so we group by conflict
 * domain. ``critical`` covers conflicts the operator most likely
 * needs to triage immediately (architecture / authority disputes);
 * ``high`` covers strategic and technical disagreements; ``standard``
 * is the default day-to-day work.
 */
type PriorityBucket = 'critical' | 'high' | 'standard'

const PRIORITY_BUCKET_TYPES: Record<PriorityBucket, readonly ConflictType[]> = {
  critical: ['architecture', 'authority'],
  high: ['strategy', 'technical'],
  standard: ['resource', 'process'],
}

const PRIORITY_OPTIONS: ReadonlyArray<{
  value: PriorityBucket | 'all'
  label: string
}> = [
  { value: 'all', label: 'All' },
  { value: 'critical', label: 'Critical' },
  { value: 'high', label: 'High' },
  { value: 'standard', label: 'Standard' },
]

const SORT_OPTIONS: ReadonlyArray<{
  value: 'priority' | 'created' | 'conflict_type'
  label: string
}> = [
  { value: 'created', label: 'Newest' },
  { value: 'priority', label: 'Priority' },
  { value: 'conflict_type', label: 'Conflict type' },
]

function priorityRank(type: ConflictType): number {
  if (PRIORITY_BUCKET_TYPES.critical.includes(type)) return 0
  if (PRIORITY_BUCKET_TYPES.high.includes(type)) return 1
  return 2
}

const STATUS_OPTIONS: ReadonlyArray<{
  value: EscalationStatus | 'all'
  label: string
}> = [
  { value: 'pending', label: 'Pending' },
  { value: 'decided', label: 'Decided' },
  { value: 'expired', label: 'Expired' },
  { value: 'cancelled', label: 'Cancelled' },
  { value: 'all', label: 'All' },
]

export default function EscalationQueuePage() {
  const escalations = useEscalationsStore((s) => s.escalations)
  const loading = useEscalationsStore((s) => s.loading)
  const loadingMore = useEscalationsStore((s) => s.loadingMore)
  const error = useEscalationsStore((s) => s.error)
  const hasMore = useEscalationsStore((s) => s.hasMore)
  const statusFilter = useEscalationsStore((s) => s.statusFilter)
  const fetchEscalations = useEscalationsStore((s) => s.fetchEscalations)
  const fetchMoreEscalations = useEscalationsStore(
    (s) => s.fetchMoreEscalations,
  )
  const setStatusFilter = useEscalationsStore((s) => s.setStatusFilter)

  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [priorityFilter, setPriorityFilter] = useState<PriorityBucket | 'all'>('all')
  const [sortKey, setSortKey] = useState<'priority' | 'created' | 'conflict_type'>('created')

  useEffect(() => {
    void fetchEscalations()
  }, [fetchEscalations])

  // Client-side priority filter + sort: applied on top of the server-
  // side status filter so the operator can narrow / re-order without
  // an extra round-trip. The bucket list is intentionally small; the
  // underlying ConflictType is the source of truth, the bucket is a
  // UX shortcut.
  const visibleEscalations = useMemo(() => {
    const filtered = priorityFilter === 'all'
      ? escalations
      : escalations.filter((row) =>
          PRIORITY_BUCKET_TYPES[priorityFilter].includes(row.escalation.conflict.type),
        )
    const sorted = [...filtered]
    switch (sortKey) {
      case 'priority':
        // Sort by bucket: critical first, then high, then standard.
        // Within a bucket, fall back to created_at desc for stability.
        sorted.sort((a, b) => {
          const ra = priorityRank(a.escalation.conflict.type)
          const rb = priorityRank(b.escalation.conflict.type)
          if (ra !== rb) return ra - rb
          return new Date(b.escalation.created_at).getTime()
            - new Date(a.escalation.created_at).getTime()
        })
        break
      case 'conflict_type':
        sorted.sort((a, b) =>
          a.escalation.conflict.type.localeCompare(b.escalation.conflict.type),
        )
        break
      case 'created':
      default:
        sorted.sort((a, b) =>
          new Date(b.escalation.created_at).getTime()
            - new Date(a.escalation.created_at).getTime(),
        )
    }
    return sorted
  }, [escalations, priorityFilter, sortKey])

  // statusFilter is server-side: when active and the server returns
  // zero rows, ``escalations.length === 0`` AND filterActive is true
  // -- but useEmptyStateProps' default rule routes a zero totalCount
  // to the "empty" branch regardless of filterActive. Force the
  // filtered copy when a filter is active and the view is empty so
  // the operator sees the right next-action ("Adjust your filters")
  // rather than the misleading "queue is empty" message.
  const filterActive =
    (statusFilter !== null && statusFilter !== undefined)
    || priorityFilter !== 'all'
  const baseEmptyStateProps = useEmptyStateProps({
    filteredCount: visibleEscalations.length,
    totalCount: escalations.length,
    filterActive,
    empty: {
      title: 'No escalations',
      description:
        'Conflicts that the autonomous resolvers cannot decide land here for human review.',
    },
    filtered: {
      title: 'No escalations match your filters',
      description:
        'Adjust the status or priority filter above to see more escalations.',
    },
  })
  const emptyStateProps =
    baseEmptyStateProps !== null
    && filterActive
    && visibleEscalations.length === 0
    && baseEmptyStateProps.title !== 'No escalations match your filters'
      ? {
          ...baseEmptyStateProps,
          title: 'No escalations match your filters',
          description:
            'Adjust the status or priority filter above to see more escalations.',
        }
      : baseEmptyStateProps

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Escalation queue"
        description="Conflicts the org has flagged for human review."
        count={visibleEscalations.length}
      />

      {error && (
        <ErrorBanner
          severity="error"
          title="Could not load escalations"
          description={error}
          onRetry={() => {
            void fetchEscalations()
          }}
        />
      )}

      {/* Status + priority filters wrapped in the shared
          SearchFilterSort layout primitive so the escalation queue
          aligns with the rest of the dashboard's list pages. */}
      <SearchFilterSort
        filters={
          <>
            <SegmentedControl
              label="Filter by status"
              value={statusFilter ?? 'all'}
              onChange={(value) => {
                // Validate against the option set before casting; a
                // malformed value (e.g. injected via a stale URL
                // fragment) drops to ``null`` instead of being
                // forwarded as an EscalationStatus that downstream
                // code does not handle.
                if (value === 'all') {
                  setStatusFilter(null)
                  return
                }
                const allowed = STATUS_OPTIONS.some((option) => option.value === value)
                if (allowed) {
                  setStatusFilter(value as EscalationStatus)
                }
              }}
              options={STATUS_OPTIONS}
              size="sm"
            />
            <SegmentedControl
              label="Filter by priority"
              value={priorityFilter}
              onChange={(value) => {
                const allowed = PRIORITY_OPTIONS.some((option) => option.value === value)
                if (allowed) {
                  setPriorityFilter(value as PriorityBucket | 'all')
                }
              }}
              options={PRIORITY_OPTIONS}
              size="sm"
            />
            <SegmentedControl
              label="Sort by"
              value={sortKey}
              onChange={(value) => {
                const allowed = SORT_OPTIONS.some((option) => option.value === value)
                if (allowed) {
                  setSortKey(value as 'priority' | 'created' | 'conflict_type')
                }
              }}
              options={SORT_OPTIONS}
              size="sm"
            />
          </>
        }
      />

      {loading && escalations.length === 0 ? (
        <div className="flex flex-col gap-grid-gap">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : !error && visibleEscalations.length === 0 ? (
        // Filter-aware copy: the operator may have a status / priority
        // filter active that hides every row, in which case "No
        // escalations" is misleading (the queue itself isn't empty,
        // just the current view). Differentiate so the empty state
        // points at the right next action.
        emptyStateProps !== null ? (
          <EmptyState {...emptyStateProps} />
        ) : null
      ) : visibleEscalations.length > 0 ? (
        <ul className="flex flex-col gap-grid-gap">
          {visibleEscalations.map((row) => {
            const e = row.escalation
            return (
              <li key={e.id}>
                <SectionCard
                  title={e.conflict.subject}
                  action={
                    <span
                      role="img"
                      aria-label={`Status ${e.status}`}
                      className={cn(
                        'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium',
                        ESCALATION_STATUS_BADGE_COLORS[e.status],
                      )}
                    >
                      {e.status.toUpperCase()}
                    </span>
                  }
                >
                  <dl className="grid grid-cols-1 gap-2 text-sm sm:grid-cols-2">
                    <div>
                      <dt className="text-text-secondary">Type</dt>
                      <dd className="text-foreground">{e.conflict.type}</dd>
                    </div>
                    <div>
                      <dt className="text-text-secondary">Detected</dt>
                      <dd className="text-foreground">
                        {formatDateTime(e.conflict.detected_at)}
                      </dd>
                    </div>
                    <div>
                      <dt className="text-text-secondary">Positions</dt>
                      <dd className="text-foreground">
                        {e.conflict.positions.length}
                      </dd>
                    </div>
                    {e.expires_at && (
                      <div>
                        <dt className="text-text-secondary">Expires</dt>
                        <dd className="text-foreground">
                          {formatDateTime(e.expires_at)}
                        </dd>
                      </div>
                    )}
                  </dl>
                  <div className="mt-grid-gap flex justify-end">
                    <Button
                      variant="secondary"
                      onClick={() => setSelectedId(e.id)}
                    >
                      Review
                    </Button>
                  </div>
                </SectionCard>
              </li>
            )
          })}
        </ul>
      ) : null}

      {hasMore && (
        <Button
          variant="secondary"
          onClick={() => {
            void fetchMoreEscalations()
          }}
          disabled={loadingMore}
        >
          {loadingMore ? 'Loading…' : 'Load more'}
        </Button>
      )}

      <EscalationDetailDrawer
        escalationId={selectedId}
        open={selectedId !== null}
        onClose={() => setSelectedId(null)}
      />
    </div>
  )
}

/**
 * Escalation queue dashboard.
 *
 * Lists pending / decided / expired / cancelled escalations with
 * cursor-paginated fetch, surfaces status + priority filters, and opens
 * a detail drawer to view the conflict and submit a decision.
 */
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SearchFilterSort } from '@/components/ui/search-filter-sort'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Skeleton } from '@/components/ui/skeleton'
import { formatDateTime } from '@/utils/format'
import { cn } from '@/lib/utils'
import { ESCALATION_STATUS_BADGE_COLORS } from '@/styles/status-colors'
import type { EscalationResponse } from '@/api/types/escalations'
import { EscalationDetailDrawer } from './escalations/EscalationDetailDrawer'
import {
  type EscalationQueue,
  PRIORITY_OPTIONS,
  SORT_OPTIONS,
  STATUS_OPTIONS,
  useEscalationQueue,
} from './escalations/useEscalationQueue'

function EscalationFilters({ q }: { q: EscalationQueue }) {
  return (
    <>
      <SegmentedControl
        label="Filter by status"
        value={q.statusFilter ?? 'all'}
        onChange={q.handleStatusChange}
        options={STATUS_OPTIONS}
        size="sm"
      />
      <SegmentedControl
        label="Filter by priority"
        value={q.priorityFilter}
        onChange={q.handlePriorityChange}
        options={PRIORITY_OPTIONS}
        size="sm"
      />
      <SegmentedControl
        label="Sort by"
        value={q.sortKey}
        onChange={q.handleSortChange}
        options={SORT_OPTIONS}
        size="sm"
      />
    </>
  )
}

function EscalationCard({ row, onReview }: { row: EscalationResponse; onReview: (id: string) => void }) {
  const e = row.escalation
  const status = e.status
  return (
    <SectionCard
      title={e.conflict.subject}
      action={
        <span
          role="img"
          aria-label={`Status ${status}`}
          className={cn(
            'inline-flex items-center rounded-md border px-2 py-0.5 text-xs font-medium',
            ESCALATION_STATUS_BADGE_COLORS[status],
          )}
        >
          {status.toUpperCase()}
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
          <dd className="text-foreground">{formatDateTime(e.conflict.detected_at)}</dd>
        </div>
        <div>
          <dt className="text-text-secondary">Positions</dt>
          <dd className="text-foreground">{e.conflict.positions.length}</dd>
        </div>
        {e.expires_at && (
          <div>
            <dt className="text-text-secondary">Expires</dt>
            <dd className="text-foreground">{formatDateTime(e.expires_at)}</dd>
          </div>
        )}
      </dl>
      <div className="mt-grid-gap flex justify-end">
        <Button variant="secondary" onClick={() => onReview(e.id)}>
          Review
        </Button>
      </div>
    </SectionCard>
  )
}

function EscalationQueueBody({ q }: { q: EscalationQueue }) {
  if (q.loading && q.escalations.length === 0) {
    return (
      <div className="flex flex-col gap-grid-gap">
        {[1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24 w-full" />
        ))}
      </div>
    )
  }
  if (!q.error && q.visibleEscalations.length === 0) {
    return q.emptyStateProps !== null ? <EmptyState {...q.emptyStateProps} /> : null
  }
  return (
    <ul className="flex flex-col gap-grid-gap">
      {q.visibleEscalations.map((row) => (
        <li key={row.escalation.id}>
          <EscalationCard row={row} onReview={q.setSelectedId} />
        </li>
      ))}
    </ul>
  )
}

export default function EscalationQueuePage() {
  const q = useEscalationQueue()

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Escalation queue"
        description="Conflicts the org has flagged for human review."
        count={q.visibleEscalations.length}
      />

      {q.error && (
        <ErrorBanner
          severity="error"
          title="Could not load escalations"
          description={q.error}
          onRetry={q.retry}
        />
      )}

      <SearchFilterSort filters={<EscalationFilters q={q} />} />

      <EscalationQueueBody q={q} />

      {q.hasMore && (
        <Button variant="secondary" onClick={q.loadMore} disabled={q.loadingMore}>
          {q.loadingMore ? 'Loading…' : 'Load more'}
        </Button>
      )}

      <EscalationDetailDrawer
        escalationId={q.selectedId}
        open={q.selectedId !== null}
        onClose={() => q.setSelectedId(null)}
      />
    </div>
  )
}

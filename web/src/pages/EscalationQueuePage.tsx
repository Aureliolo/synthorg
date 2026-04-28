/**
 * Escalation queue dashboard (#1418).
 *
 * Lists pending / decided / expired / cancelled escalations with
 * cursor-paginated fetch, surfaces a status filter, and opens a
 * detail drawer to view the underlying conflict and submit a
 * decision.
 */
import { useEffect, useState } from 'react'
import { Button } from '@/components/ui/button'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { SectionCard } from '@/components/ui/section-card'
import { SegmentedControl } from '@/components/ui/segmented-control'
import { Skeleton } from '@/components/ui/skeleton'
import { useEscalationsStore } from '@/stores/escalations'
import { formatDateTime } from '@/utils/format'
import { EscalationDetailDrawer } from './escalations/EscalationDetailDrawer'
import type { EscalationStatus } from '@/api/types/escalations'
import { cn } from '@/lib/utils'

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

const STATUS_BADGE_CLASS: Record<EscalationStatus, string> = {
  pending: 'bg-warning/10 text-warning border-warning/20',
  decided: 'bg-success/10 text-success border-success/20',
  expired: 'bg-danger/10 text-danger border-danger/20',
  cancelled: 'bg-surface text-text-secondary border-border',
}

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

  useEffect(() => {
    void fetchEscalations()
  }, [fetchEscalations])

  return (
    <div className="flex flex-col gap-section-gap">
      <ListHeader title="Escalation queue" count={escalations.length} />

      <SegmentedControl
        label="Filter by status"
        value={statusFilter ?? 'all'}
        onChange={(value) => {
          setStatusFilter(value === 'all' ? null : (value as EscalationStatus))
        }}
        options={STATUS_OPTIONS}
        size="sm"
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

      {loading && escalations.length === 0 ? (
        <div className="flex flex-col gap-grid-gap">
          {[1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-24 w-full" />
          ))}
        </div>
      ) : escalations.length === 0 ? (
        <EmptyState
          title="No escalations"
          description="Conflicts that the autonomous resolvers cannot decide land here for human review."
        />
      ) : (
        <ul className="flex flex-col gap-grid-gap">
          {escalations.map((row) => {
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
                        STATUS_BADGE_CLASS[e.status],
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
      )}

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

import { useCallback, useMemo } from 'react'

import { ListChecks } from 'lucide-react'

import type { Plan, PlanStatus } from '@/api/types/plans'
import { BulkDeleteControls } from '@/components/ui/bulk-delete-controls'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useBulkSelection } from '@/hooks/use-bulk-selection'
import { useListPagination } from '@/hooks/use-list-pagination'
import { judgedRoles, useOrgRoster } from '@/hooks/useOrgRoster'
import { usePlansData } from '@/hooks/usePlansData'
import { usePlansStore } from '@/stores/plans'

import { PlanListItem } from './plans/PlanListItem'
import { PlansSkeleton } from './plans/PlansSkeleton'

// Plans awaiting a decision surface first, then failed plans (which need the
// operator's attention / a re-run), then in-flight and decided plans by recency.
// The tail stages rank above executing: a plan being assembled or scored is the
// closest to delivery and the likeliest to need a look. The skeleton ranks just
// below executing and above approved: it is running work rather than a plan
// waiting to start, and it is the furthest of the running statuses from
// delivery. Approved-but-not-yet-dispatched follows, and completed plans sink
// below every live status.
const STATUS_ORDER: Record<PlanStatus, number> = {
  pending_review: 0,
  failed: 1,
  planning: 2,
  draft: 3,
  evaluating: 4,
  integrating: 5,
  executing: 6,
  skeleton: 7,
  approved: 8,
  rejected: 9,
  superseded: 10,
  completed: 11,
}

function sortForReview(plans: readonly Plan[]): readonly Plan[] {
  return [...plans].sort((a, b) => {
    const byStatus = STATUS_ORDER[a.status] - STATUS_ORDER[b.status]
    if (byStatus !== 0) return byStatus
    return b.updated_at.localeCompare(a.updated_at)
  })
}

export default function PlansPage() {
  const { filteredPlans, totalPlans, loading, error, wsConnected, wsSetupError } =
    usePlansData()
  // Fetched once for the whole inbox: the "to review" count on a row is the
  // same derivation the detail page runs, so a row that omitted the roster
  // would advertise nothing to review on a plan the detail page flags.
  const roster = judgedRoles(useOrgRoster())

  const ordered = useMemo(() => sortForReview(filteredPlans), [filteredPlans])

  // URL-persisted browser pagination over the fully-loaded, sorted set: the
  // review inbox filters/sorts across every plan, so a server cursor would
  // only ever see one slice.
  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize } =
    useListPagination({ items: ordered, namespace: 'plans' })

  // The rendered page, not the whole ordered set: selection is held against
  // what is on screen, so feeding it every match would let the count and the
  // confirm dialog cover rows on other pages the operator cannot see.
  const visibleIds = useMemo(
    () => paginatedItems.map((plan) => plan.id),
    [paginatedItems],
  )
  const selection = useBulkSelection(
    visibleIds,
    useCallback(
      (ids: readonly string[]) => usePlansStore.getState().batchDeletePlans(ids),
      [],
    ),
  )

  if (loading && totalPlans === 0) {
    return <PlansSkeleton />
  }

  return (
    <div className="space-y-section-gap">
      <ListHeader
        title="Plan Review"
        count={ordered.length}
        description="Review, rework, and decide on the plans the org proposes before any team builds."
      />

      {error !== null && (
        <ErrorBanner
          severity="error"
          title="Could not load plans"
          description={error}
        />
      )}
      {!wsConnected && !loading && (
        <ErrorBanner
          variant="offline"
          title="Real-time updates disconnected"
          description={wsSetupError ?? 'Plans may be stale until the connection recovers.'}
        />
      )}

      {ordered.length === 0 ? (
        <EmptyState
          icon={ListChecks}
          title="No plans to review"
          description="When the org decomposes an objective into a plan, it lands here for your review before any team is mobilised."
        />
      ) : (
        <>
          <div className="flex flex-col gap-2">
            {paginatedItems.map((plan) => (
              <PlanListItem
                key={plan.id}
                plan={plan}
                roster={roster}
                onToggleSelect={selection.toggle}
                selected={selection.visibleSelected.has(plan.id)}
              />
            ))}
          </div>
          <Pagination
            page={page}
            pageSize={pageSize}
            total={totalItems}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
          <BulkDeleteControls
            selection={selection}
            noun={{ one: 'Plan', many: 'plans' }}
            description="This permanently removes the selected plans and retires the approvals parked against them. A plan whose items are still building is refused and stays; the rest go. This action cannot be undone."
            ariaLabel="Plan bulk actions"
          />
        </>
      )}
    </div>
  )
}

import { useMemo } from 'react'

import { ListChecks } from 'lucide-react'

import type { Plan, PlanStatus } from '@/api/types/plans'
import { EmptyState } from '@/components/ui/empty-state'
import { ErrorBanner } from '@/components/ui/error-banner'
import { ListHeader } from '@/components/ui/list-header'
import { Pagination } from '@/components/ui/pagination'
import { useListPagination } from '@/hooks/use-list-pagination'
import { usePlansData } from '@/hooks/usePlansData'

import { PlanListItem } from './plans/PlanListItem'
import { PlansSkeleton } from './plans/PlansSkeleton'

// Plans awaiting a decision surface first; then the rest by recency.
const STATUS_ORDER: Record<PlanStatus, number> = {
  pending_review: 0,
  draft: 1,
  approved: 2,
  rejected: 3,
  superseded: 4,
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

  const ordered = useMemo(() => sortForReview(filteredPlans), [filteredPlans])

  // URL-persisted browser pagination over the fully-loaded, sorted set: the
  // review inbox filters/sorts across every plan, so a server cursor would
  // only ever see one slice.
  const { page, pageSize, totalItems, paginatedItems, setPage, setPageSize } =
    useListPagination({ items: ordered, namespace: 'plans' })

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
              <PlanListItem key={plan.id} plan={plan} />
            ))}
          </div>
          <Pagination
            page={page}
            pageSize={pageSize}
            total={totalItems}
            onPageChange={setPage}
            onPageSizeChange={setPageSize}
          />
        </>
      )}
    </div>
  )
}

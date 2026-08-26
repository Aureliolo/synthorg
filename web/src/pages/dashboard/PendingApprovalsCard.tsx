import { Link } from 'react-router'
import { ChevronRight, ClipboardCheck } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { Skeleton } from '@/components/ui/skeleton'
import { ROUTES } from '@/router/routes'

export interface PendingApprovalsCardProps {
  /** Decisions taken in the Approvals inbox. */
  count: number
  /** Plans awaiting review, which are decided on the Plan Review page. */
  planReviewCount?: number
  loading?: boolean
}

interface PendingRowProps {
  count: number
  to: string
  noun: string
  plural: string
}

function PendingRow({ count, to, noun, plural }: PendingRowProps) {
  const label = count === 1 ? noun : plural
  const verb = count === 1 ? 'awaits' : 'await'
  return (
    <Link
      to={to}
      className="-m-2 flex items-center justify-between rounded-md p-2 transition-colors hover:bg-card-hover focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
      aria-label={`${String(count)} ${label} ${verb} your decision; open them`}
    >
      <div>
        <div className="font-mono text-metric font-bold leading-tight text-foreground">
          {count}
        </div>
        <div className="text-sm text-muted-foreground">
          {label} {verb} your decision
        </div>
      </div>
      <ChevronRight className="size-5 shrink-0 text-muted-foreground" aria-hidden="true" />
    </Link>
  )
}

/**
 * Dashboard panel surfacing what awaits the operator's decision.
 *
 * A row per destination, because there are two and they are decided in
 * different places: a plan review gathers decisions on the Plan Review page,
 * everything else is a binary call taken in the Approvals inbox. Summing them
 * into one number under one link is what put "1 item awaits your decision"
 * above a link to a page that rendered "no approvals": the count included the
 * plan review and the page it pointed at excludes them.
 *
 * Presentational: both counts are supplied by the caller, from the selectors
 * the sidebar badges read.
 */
export function PendingApprovalsCard({
  count,
  planReviewCount = 0,
  loading = false,
}: PendingApprovalsCardProps) {
  const total = count + planReviewCount
  // Hold the loading state until the shared fetch settles so an in-progress
  // load never flashes the "No approvals waiting" empty state.
  if (loading && total === 0) {
    return (
      <SectionCard title="Pending Approvals" icon={ClipboardCheck}>
        <Skeleton className="h-10 w-full" />
      </SectionCard>
    )
  }
  return (
    <SectionCard title="Pending Approvals" icon={ClipboardCheck}>
      {total === 0 ? (
        <EmptyState
          icon={ClipboardCheck}
          title="No approvals waiting"
          description="Plans and actions needing your decision will appear here."
        />
      ) : (
        <div className="flex flex-col gap-3">
          {count > 0 && (
            <PendingRow
              count={count}
              to={ROUTES.APPROVALS}
              noun="item"
              plural="items"
            />
          )}
          {planReviewCount > 0 && (
            <PendingRow
              count={planReviewCount}
              to={ROUTES.PLANS}
              noun="plan"
              plural="plans"
            />
          )}
        </div>
      )}
    </SectionCard>
  )
}

import { ChevronRight, ListTree } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router'

import type { Plan } from '@/api/types/plans'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { StatusPill } from '@/components/ui/status-pill'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'
import { derivePlanStats, planDetailPath } from '@/utils/plans'

/** Review inbox rows only summarise; critical-path membership is a detail-page
 * signal, so the row stats intentionally ignore it (empty path set). */
const NO_CRITICAL_PATH: ReadonlySet<string> = new Set()

export interface PlanListItemProps {
  plan: Plan
  className?: string
}

/** A single plan row in the review inbox, linking to its detail workspace. */
export function PlanListItem({ plan, className }: PlanListItemProps) {
  const itemCount = plan.items.length
  const stats = useMemo(
    () => derivePlanStats(plan.items, NO_CRITICAL_PATH),
    [plan.items],
  )
  return (
    <Link
      to={planDetailPath(plan.id)}
      className={cn(
        'flex items-center gap-3 rounded-md border border-border p-card',
        'transition-colors hover:bg-surface focus-visible:bg-surface',
        className,
      )}
    >
      <ListTree className="size-4 shrink-0 text-text-secondary" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2">
          <span className="truncate text-sm font-medium text-foreground">
            {plan.objective_id}
          </span>
          <PlanStatusBadge status={plan.status} />
          {stats.flaggedItems > 0 && (
            <StatusPill tone="warning" className="shrink-0">
              {stats.flaggedItems} to review
            </StatusPill>
          )}
        </div>
        <div className="mt-0.5 flex items-center gap-2 text-xs text-text-secondary">
          <span>{plan.project}</span>
          <span aria-hidden="true">·</span>
          <span>
            {itemCount} item{itemCount === 1 ? '' : 's'}
          </span>
          <span aria-hidden="true">·</span>
          <span>v{plan.version}</span>
          <span aria-hidden="true">·</span>
          <span>updated {formatRelativeTime(plan.updated_at)}</span>
        </div>
      </div>
      <ChevronRight
        className="size-4 shrink-0 text-muted-foreground"
        aria-hidden="true"
      />
    </Link>
  )
}

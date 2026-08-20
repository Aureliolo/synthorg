import { ChevronRight, ListTree } from 'lucide-react'
import { useMemo } from 'react'
import { Link } from 'react-router'

import type { Plan } from '@/api/types/plans'
import { Checkbox } from '@/components/ui/checkbox'
import { PlanStatusBadge } from '@/components/ui/plan-status-badge'
import { StatusPill } from '@/components/ui/status-pill'
import { cn } from '@/lib/utils'
import { formatRelativeTime } from '@/utils/format'
import { planSolicitsReview } from '@/utils/plan-status'
import { criticalPathFor, derivePlanStats, planDetailPath } from '@/utils/plans'

export interface PlanListItemProps {
  plan: Plan
  /**
   * The roles the org staffs, or `undefined` while unknown. Supplied by the
   * page rather than fetched per row, so every row on a page counts against
   * the same roster the detail page will.
   */
  roster: ReadonlySet<string> | undefined
  /** When defined, the row carries a selection checkbox. */
  onToggleSelect?: ((id: string) => void) | undefined
  selected?: boolean | undefined
  className?: string
}

/**
 * What the row says about the plan.
 *
 * `flaggedItems` arrives already zeroed on a plan past its review decision:
 * whether a review is still being asked for is the plan's question, not the
 * item counter's.
 */
function PlanRowSummary({
  plan,
  flaggedItems,
}: {
  plan: Plan
  flaggedItems: number
}) {
  const itemCount = plan.items.length
  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-2">
        <span className="truncate text-sm font-medium text-foreground">
          {plan.objective_title}
        </span>
        <PlanStatusBadge status={plan.status} />
        {/* The status says what the org last did; it cannot say the
            initiative has stopped and is waiting on this operator, and a
            row reading "executing" with every item dead is the board
            telling them work is in flight when none is. */}
        {plan.pending_decision !== null && (
          <StatusPill tone="warning" className="shrink-0">
            Awaiting your decision
          </StatusPill>
        )}
        {flaggedItems > 0 && (
          <StatusPill tone="warning" className="shrink-0">
            {flaggedItems} to review
          </StatusPill>
        )}
      </div>
      {plan.pending_decision !== null && (
        <p className="mt-0.5 truncate text-xs text-text-secondary">
          {plan.pending_decision.reason}
        </p>
      )}
      <div className="mt-0.5 flex items-center gap-2 text-xs text-text-secondary">
        <span>{plan.project_name}</span>
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
  )
}

/** A single plan row in the review inbox, linking to its detail workspace. */
export function PlanListItem({
  plan,
  roster,
  onToggleSelect,
  selected = false,
  className,
}: PlanListItemProps) {
  // The same derivation the detail page runs, critical path included. Passing
  // an empty path here made the row and the page it links to disagree about
  // one number under one label: a row read "3 to review" and its own detail
  // page headlined 6, because a critical-path item flags on one and not the
  // other. Both are derived from the plan's own items, so there is nothing to
  // fetch and no reason for two answers.
  const criticalPath = useMemo(
    () => criticalPathFor(plan.items, plan.task_structure),
    [plan.items, plan.task_structure],
  )
  const stats = useMemo(
    () => derivePlanStats(plan.items, criticalPath, roster),
    [plan.items, criticalPath, roster],
  )
  const solicitsReview = planSolicitsReview(plan.status)
  return (
    <div className="flex items-center gap-2">
      {/* Outside the link, not inside it: a control nested in an anchor is
          the invalid markup that made a whole card surface inert elsewhere
          on this dashboard. */}
      {onToggleSelect && (
        <Checkbox
          checked={selected}
          onCheckedChange={() => onToggleSelect(plan.id)}
          aria-label={`Select plan ${plan.objective_title}`}
        />
      )}
    <Link
      to={planDetailPath(plan.id)}
      className={cn(
        'flex flex-1 items-center gap-3 rounded-md border p-card',
        'transition-colors hover:bg-surface focus-visible:bg-surface',
        selected ? 'border-accent ring-2 ring-accent/30' : 'border-border',
        className,
      )}
    >
      <ListTree className="size-4 shrink-0 text-text-secondary" aria-hidden="true" />
      <PlanRowSummary
        plan={plan}
        flaggedItems={solicitsReview ? stats.flaggedItems : 0}
      />
      <ChevronRight
        className="size-4 shrink-0 text-muted-foreground"
        aria-hidden="true"
      />
    </Link>
    </div>
  )
}

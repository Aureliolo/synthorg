import { CircleCheck, Target, TriangleAlert } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'
import { derivePlanCoverage } from '@/utils/plans'

function CoverageRow({
  criterion,
  coveredBy,
}: {
  criterion: string
  coveredBy: readonly string[]
}) {
  const covered = coveredBy.length > 0
  return (
    <li className="space-y-1 rounded-md border border-border p-2">
      <div className="flex items-start gap-1.5">
        {covered ? (
          <CircleCheck
            className="mt-0.5 size-3.5 shrink-0 text-success"
            aria-hidden="true"
          />
        ) : (
          <TriangleAlert
            className="mt-0.5 size-3.5 shrink-0 text-warning"
            aria-hidden="true"
          />
        )}
        <span className="text-sm text-foreground">{criterion}</span>
      </div>
      {covered ? (
        <p className="pl-5 text-xs text-text-secondary">
          Advanced by {coveredBy.join(', ')}
        </p>
      ) : (
        <p className="pl-5 text-xs text-warning">No item advances this criterion.</p>
      )}
    </li>
  )
}

/**
 * Success-criteria coverage: each objective acceptance criterion and the plan
 * items that advance it, flagging any criterion nothing covers, so the reviewer
 * can see the plan actually delivers the objective. Hidden when the objective
 * declared no criteria (nothing to check).
 */
export function PlanCoveragePanel({ plan }: { plan: Plan }) {
  if (plan.objective_criteria.length === 0) return null
  const coverage = derivePlanCoverage(plan.objective_criteria, plan.items)
  const complete = coverage.uncovered.length === 0
  return (
    <SectionCard
      title="Success-criteria coverage"
      icon={Target}
      action={
        <StatusPill tone={complete ? 'success' : 'warning'}>
          {coverage.covered}/{coverage.total} covered
        </StatusPill>
      }
    >
      <ul className="flex flex-col gap-2">
        {coverage.entries.map((entry) => (
          <CoverageRow
            key={entry.criterion}
            criterion={entry.criterion}
            coveredBy={entry.coveredBy}
          />
        ))}
      </ul>
    </SectionCard>
  )
}

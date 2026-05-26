import { AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { BudgetState } from './department-budget'

export function BudgetTotalChip({ budget }: { budget: BudgetState }) {
  return (
    <div
      className={cn(
        'inline-flex items-center gap-2 rounded-md border px-card py-1 text-compact font-medium',
        budget.isOver && 'border-danger/40 bg-danger/5 text-danger',
        budget.isUnder && 'border-warning/40 bg-warning/5 text-warning',
        !budget.off && 'border-border bg-card text-text-secondary',
      )}
      role="status"
      aria-live="polite"
    >
      <span>Total budget allocated: {budget.rounded}%</span>
      {budget.off && <AlertTriangle className="size-3.5" aria-hidden="true" />}
    </div>
  )
}

export function BudgetWarningAlert({
  budget,
  budgetTotal,
}: {
  budget: BudgetState
  budgetTotal: number
}) {
  if (!budget.off) return null
  return (
    <div
      role="alert"
      className={cn(
        'flex items-start gap-3 rounded-lg border p-card text-sm',
        budget.isOver
          ? 'border-danger/40 bg-danger/5 text-danger'
          : 'border-warning/40 bg-warning/5 text-warning',
      )}
    >
      <AlertTriangle className="size-4 shrink-0" aria-hidden="true" />
      <div className="flex-1">
        {budget.isOver ? (
          <>
            <div className="font-semibold">
              Department budgets sum to {budget.rounded}% (over 100%).
            </div>
            <p className="mt-1 text-compact text-danger/80">
              This usually happens after adding a team pack or a new department without rebalancing
              the existing allocations. Open the departments below and reduce their budget percents
              so the total is 100%.
            </p>
          </>
        ) : (
          <>
            <div className="font-semibold">
              Department budgets sum to {budget.rounded}% (under 100%).
            </div>
            <p className="mt-1 text-compact text-warning/80">
              The remaining {Math.round((100 - budgetTotal) * 10) / 10}% is unallocated. Increase one
              of the departments below or add a new one to cover the gap.
            </p>
          </>
        )}
      </div>
    </div>
  )
}

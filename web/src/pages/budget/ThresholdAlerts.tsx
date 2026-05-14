import { AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { ThresholdZone } from '@/utils/budget'
import type { OverviewMetrics } from '@/api/types/analytics'
import type { BudgetConfig } from '@/api/types/budget'

export interface ThresholdAlertsProps {
  zone: ThresholdZone
  budgetConfig: BudgetConfig | null
  overview: OverviewMetrics | null
}

export function ThresholdAlerts({ zone, budgetConfig, overview }: ThresholdAlertsProps) {
  if (zone === 'normal' || !budgetConfig || !overview) return null

  const isAmber = zone === 'amber'
  const isDanger = zone === 'red' || zone === 'critical'
  const usedPct = Number.isInteger(overview.budget_used_percent)
    ? String(overview.budget_used_percent)
    : overview.budget_used_percent.toFixed(1)

  let message: string
  if (zone === 'amber') {
    message = `Budget usage at ${usedPct}%: warning threshold (${budgetConfig.alerts.warn_at}%) reached`
  } else if (zone === 'red') {
    message = `Budget usage at ${usedPct}%: critical threshold (${budgetConfig.alerts.critical_at}%) reached`
  } else {
    message = `Budget hard stop at ${budgetConfig.alerts.hard_stop_at}% reached. Spending halted.`
  }

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-lg border p-card text-sm',
        isAmber && 'border-warning/30 bg-warning/5 text-warning',
        isDanger && 'border-danger/30 bg-danger/5 text-danger',
      )}
      role="alert"
    >
      <AlertTriangle
        className={cn(
          'size-4 shrink-0',
          zone === 'critical' && 'animate-pulse',
        )}
        aria-hidden="true"
      />
      <span>{message}</span>
    </div>
  )
}

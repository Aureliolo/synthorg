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

function formatUsedPct(usedPercent: number): string {
  return Number.isInteger(usedPercent) ? String(usedPercent) : usedPercent.toFixed(1)
}

function buildThresholdMessage(
  zone: Exclude<ThresholdZone, 'normal'>,
  usedPct: string,
  alerts: BudgetConfig['alerts'],
): string {
  if (zone === 'amber') {
    return `Budget usage at ${usedPct}%: warning threshold (${alerts.warn_at}%) reached`
  }
  if (zone === 'red') {
    return `Budget usage at ${usedPct}%: critical threshold (${alerts.critical_at}%) reached`
  }
  return `Budget hard stop at ${alerts.hard_stop_at}% reached. Spending halted.`
}

export function ThresholdAlerts({ zone, budgetConfig, overview }: ThresholdAlertsProps) {
  if (zone === 'normal' || !budgetConfig || !overview) return null

  const isAmber = zone === 'amber'
  const isDanger = zone === 'red' || zone === 'critical'
  const message = buildThresholdMessage(
    zone,
    formatUsedPct(overview.budget_used_percent),
    budgetConfig.alerts,
  )

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
        className={cn('size-4 shrink-0', zone === 'critical' && 'animate-pulse')}
        aria-hidden="true"
      />
      <span>{message}</span>
    </div>
  )
}

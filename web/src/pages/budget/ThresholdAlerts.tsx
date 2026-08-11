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
  if (zone === 'unmeasurable') {
    return (
      'Budget thresholds are not being evaluated: the percentage they compare ' +
      'against does not measure this period’s spend.'
    )
  }
  if (zone === 'amber') {
    return `Budget usage at ${usedPct}%: warning threshold (${alerts.warn_at}%) reached`
  }
  if (zone === 'red') {
    return `Budget usage at ${usedPct}%: critical threshold (${alerts.critical_at}%) reached`
  }
  return `Budget hard stop at ${alerts.hard_stop_at}% reached. Spending halted.`
}

const ZONE_TONE: Record<Exclude<ThresholdZone, 'normal'>, string> = {
  // `unmeasurable` shares the warning tone rather than the danger one: no
  // threshold was crossed, the thresholds simply cannot be evaluated.
  unmeasurable: 'border-warning/30 bg-warning/5 text-warning',
  amber: 'border-warning/30 bg-warning/5 text-warning',
  red: 'border-danger/30 bg-danger/5 text-danger',
  critical: 'border-danger/30 bg-danger/5 text-danger',
}

export function ThresholdAlerts({ zone, budgetConfig, overview }: ThresholdAlertsProps) {
  if (zone === 'normal' || !budgetConfig || !overview) return null

  const message = buildThresholdMessage(
    zone,
    formatUsedPct(overview.budget_used_percent),
    budgetConfig.alerts,
  )

  return (
    <div
      className={cn(
        'flex items-center gap-2 rounded-lg border p-card text-sm',
        ZONE_TONE[zone],
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

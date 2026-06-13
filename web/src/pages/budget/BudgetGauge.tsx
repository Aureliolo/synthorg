import { DollarSign } from 'lucide-react'
import { SectionCard } from '@/components/ui/section-card'
import { ProgressGauge } from '@/components/ui/progress-gauge'
import { formatCurrency } from '@/utils/format'
import { computeExhaustionDate } from '@/utils/budget'

export interface BudgetGaugeProps {
  usedPercent: number
  budgetRemaining: number
  daysUntilExhausted: number | null
  currency?: string | undefined
}

export function BudgetGauge({
  usedPercent,
  budgetRemaining,
  daysUntilExhausted,
  currency,
}: BudgetGaugeProps) {
  const exhaustionDate = computeExhaustionDate(daysUntilExhausted)

  return (
    <SectionCard title="Budget Status" icon={DollarSign}>
      <div className="flex flex-col items-center gap-3">
        <ProgressGauge
          size="md"
          value={Math.max(0, 100 - usedPercent)}
          label="Budget Health"
        />
        <div className="text-center">
          <p className="font-mono text-lg font-semibold text-foreground">
            {formatCurrency(budgetRemaining, currency)}
          </p>
          <p className="text-xs text-muted-foreground">remaining</p>
        </div>
        <p className="text-xs text-text-muted">
          {exhaustionDate
            ? `Projected exhaustion: ${exhaustionDate}`
            : 'No exhaustion projected'}
        </p>
      </div>
    </SectionCard>
  )
}

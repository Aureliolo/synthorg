import { StatPill } from '@/components/ui/stat-pill'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { formatCurrency } from '@/utils/format'
import { cn } from '@/lib/utils'

interface DepartmentStatsBarProps {
  activeCount: number
  cost7d: number | null
  currency?: string
  className?: string
}

/**
 * Compact per-dept stats. The agent count is intentionally omitted -- the
 * header's `👤N` badge already carries it, so repeating it here only widened
 * the row until the cost pill overflowed the card. Active + cost are the two
 * values unique to this row.
 */
export function DepartmentStatsBar({
  activeCount,
  cost7d,
  currency = DEFAULT_CURRENCY,
  className,
}: DepartmentStatsBarProps) {
  return (
    <div className={cn('flex flex-nowrap gap-1.5', className)} data-testid="dept-stats-bar">
      <StatPill label="Active" value={activeCount} />
      {cost7d !== null && Number.isFinite(cost7d) && (
        <StatPill label="Cost (7d)" value={formatCurrency(cost7d, currency)} />
      )}
    </div>
  )
}

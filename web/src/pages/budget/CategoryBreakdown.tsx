import { SectionCard } from '@/components/ui/section-card'
import { EmptyState } from '@/components/ui/empty-state'
import { formatCurrency } from '@/utils/format'
import { Layers } from 'lucide-react'
import type { CategoryBucket, CategoryRatio } from '@/utils/budget'

export interface CategoryBreakdownProps {
  ratio: CategoryRatio
  currency?: string | undefined
}

const CATEGORIES: {
  key: keyof CategoryRatio
  label: string
  barClass: string
  dotClass: string
}[] = [
  { key: 'productive', label: 'Productive', barClass: 'bg-success', dotClass: 'bg-success' },
  { key: 'coordination', label: 'Coordination', barClass: 'bg-accent', dotClass: 'bg-accent' },
  { key: 'system', label: 'System', barClass: 'bg-warning', dotClass: 'bg-warning' },
  { key: 'embedding', label: 'Embedding', barClass: 'bg-accent-dim', dotClass: 'bg-accent-dim' },
  { key: 'uncategorized', label: 'Uncategorized', barClass: 'bg-border', dotClass: 'bg-border' },
]

function isAllZero(ratio: CategoryRatio): boolean {
  return (
    ratio.productive.count + ratio.coordination.count +
    ratio.system.count + ratio.embedding.count +
    ratio.uncategorized.count === 0
  )
}

function CategoryLegendRow({ label, dotClass, bucket, currency }: {
  label: string
  dotClass: string
  bucket: CategoryBucket
  currency?: string | undefined
}) {
  return (
    <div className="flex items-center gap-2">
      <span className={`size-2 shrink-0 rounded-full ${dotClass}`} />
      <span className="text-xs text-text-secondary">{label}</span>
      <span className="ml-auto font-mono text-xs text-foreground">
        {formatCurrency(bucket.cost, currency)}
      </span>
      <span className="font-mono text-[10px] text-text-muted">
        {bucket.percent.toFixed(1)}%
      </span>
    </div>
  )
}

export function CategoryBreakdown({ ratio, currency }: CategoryBreakdownProps) {
  const empty = isAllZero(ratio)

  return (
    <SectionCard title="Cost Categories" icon={Layers}>
      {empty ? (
        <EmptyState
          icon={Layers}
          title="No cost data"
          description="Category breakdown will appear as agents consume tokens."
        />
      ) : (
        <div className="space-y-section-gap">
          {/* Stacked bar */}
          <div className="flex h-6 w-full overflow-hidden rounded-full">
            {CATEGORIES.map((cat) => (
              <div
                key={cat.key}
                style={{ width: `${ratio[cat.key].percent}%` }}
                className={`${cat.barClass} transition-all duration-[var(--so-transition-slow)]`}
                data-testid={`bar-${cat.key}`}
              />
            ))}
          </div>

          {/* Legend */}
          <div className="grid grid-cols-2 gap-grid-gap">
            {CATEGORIES.map((cat) => (
              <CategoryLegendRow
                key={cat.key}
                label={cat.label}
                dotClass={cat.dotClass}
                bucket={ratio[cat.key]}
                currency={currency}
              />
            ))}
          </div>
        </div>
      )}
    </SectionCard>
  )
}

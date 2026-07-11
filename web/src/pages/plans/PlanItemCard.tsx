import type { PlanItem } from '@/api/types/plans'

export interface PlanItemCardProps {
  item: PlanItem
  index: number
}

/** Read-only view of a single plan item within the review workspace. */
export function PlanItemCard({ item, index }: PlanItemCardProps) {
  return (
    <div className="space-y-2 rounded-md border border-border p-card">
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-foreground">
          {index + 1}. {item.title}
        </span>
        {item.owner !== null && (
          <span className="shrink-0 text-xs text-text-secondary">{item.owner}</span>
        )}
      </div>
      <p className="text-sm text-text-secondary">{item.description}</p>
      <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <span>complexity: {item.estimated_complexity}</span>
        <span>stakes: {item.stakes}</span>
        {item.dependencies.length > 0 && (
          <span>depends on: {item.dependencies.join(', ')}</span>
        )}
      </div>
    </div>
  )
}

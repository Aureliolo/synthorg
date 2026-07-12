import { CircleCheck, GitBranch, Package, UserRound } from 'lucide-react'

import type { PlanItem } from '@/api/types/plans'
import { StatusPill } from '@/components/ui/status-pill'
import { cn } from '@/lib/utils'
import {
  COMPLEXITY_LABEL,
  COMPLEXITY_TONE,
  STAKES_LABEL,
  STAKES_TONE,
  dependencyTitles,
  planItemAnchorId,
} from '@/utils/plans'

export interface PlanItemCardProps {
  item: PlanItem
  index: number
  onCriticalPath: boolean
  titleById: ReadonlyMap<string, string>
  className?: string
}

function ItemPills({ item, onCriticalPath }: { item: PlanItem; onCriticalPath: boolean }) {
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <StatusPill tone={COMPLEXITY_TONE[item.estimated_complexity]}>
        {COMPLEXITY_LABEL[item.estimated_complexity]} effort
      </StatusPill>
      <StatusPill tone={STAKES_TONE[item.stakes]}>
        {STAKES_LABEL[item.stakes]} stakes
      </StatusPill>
      {onCriticalPath && (
        <StatusPill tone="accent" icon={GitBranch}>
          Critical path
        </StatusPill>
      )}
      {item.owner === null && (
        <StatusPill tone="warning" icon={UserRound}>
          Unassigned
        </StatusPill>
      )}
    </div>
  )
}

function AcceptanceCriteria({ criteria }: { criteria: readonly string[] }) {
  if (criteria.length === 0) {
    return (
      <p className="flex items-center gap-1.5 text-xs text-warning">
        <CircleCheck className="size-3.5 shrink-0" aria-hidden="true" />
        No acceptance criteria: nothing defines when this item is done.
      </p>
    )
  }
  return (
    <div>
      <span className="text-micro uppercase tracking-wide text-muted-foreground">
        Done when
      </span>
      <ul className="mt-1 space-y-1">
        {criteria.map((line) => (
          <li key={line} className="flex items-start gap-1.5 text-xs text-text-secondary">
            <CircleCheck
              className="mt-0.5 size-3.5 shrink-0 text-success"
              aria-hidden="true"
            />
            <span>{line}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}

function ChipRow({
  label,
  icon: Icon,
  values,
}: {
  label: string
  icon: typeof Package
  values: readonly string[]
}) {
  if (values.length === 0) return null
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <span className="inline-flex items-center gap-1 text-micro uppercase tracking-wide text-muted-foreground">
        <Icon className="size-3.5" aria-hidden="true" />
        {label}
      </span>
      {values.map((value) => (
        <span
          key={value}
          className="rounded-sm border border-border bg-surface px-1.5 py-0.5 text-micro text-text-secondary"
        >
          {value}
        </span>
      ))}
    </div>
  )
}

/** Read-only review card for a single plan item, surfacing its review signals. */
export function PlanItemCard({
  item,
  index,
  onCriticalPath,
  titleById,
  className,
}: PlanItemCardProps) {
  const deps = dependencyTitles(item, titleById)
  return (
    <section
      id={planItemAnchorId(item.id)}
      className={cn(
        'scroll-mt-4 space-y-3 rounded-md border p-card',
        onCriticalPath ? 'border-accent/40 bg-accent/[0.03]' : 'border-border',
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm font-medium text-foreground">
          {index + 1}. {item.title}
        </span>
        {item.owner !== null && (
          <span className="shrink-0 text-xs text-text-secondary">{item.owner}</span>
        )}
      </div>
      <ItemPills item={item} onCriticalPath={onCriticalPath} />
      <p className="text-sm text-text-secondary">{item.description}</p>
      <AcceptanceCriteria criteria={item.acceptance_criteria} />
      <ChipRow label="Delivers" icon={Package} values={item.expected_artifacts} />
      <ChipRow label="Needs skills" icon={UserRound} values={item.required_skills} />
      {deps.length > 0 && (
        <p className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground">
          <GitBranch className="size-3.5 shrink-0" aria-hidden="true" />
          <span className="uppercase tracking-wide">Depends on</span>
          <span className="text-text-secondary">{deps.join(', ')}</span>
        </p>
      )}
    </section>
  )
}

import { GitCompare, Minus, PencilLine, Plus } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatusPill } from '@/components/ui/status-pill'
import { derivePlanDiff, type ItemDiff } from '@/utils/plan-diff'

const CHANGE_ICON = { added: Plus, removed: Minus, modified: PencilLine } as const
const CHANGE_TONE = { added: 'success', removed: 'danger', modified: 'warning' } as const

function DiffRow({ diff }: { diff: ItemDiff }) {
  const Icon = CHANGE_ICON[diff.change]
  return (
    <li className="flex flex-wrap items-center gap-2 rounded-md border border-border p-2">
      <StatusPill tone={CHANGE_TONE[diff.change]} icon={Icon}>
        {diff.change}
      </StatusPill>
      <span className="text-sm text-foreground">{diff.title}</span>
      {diff.changedFields.length > 0 && (
        <span className="text-xs text-text-secondary">
          changed: {diff.changedFields.join(', ')}
        </span>
      )}
    </li>
  )
}

/**
 * How the current revision differs from the version before it: items added,
 * removed, or modified (and which fields), so a reviewer can see how a rework
 * addressed the panel's concerns. Hidden until the plan has a prior version.
 */
export function PlanVersionDiff({ plan }: { plan: Plan }) {
  const previous = plan.version_history.at(-1)
  if (previous === undefined) return null
  const diff = derivePlanDiff(previous, { items: plan.items, version: plan.version })
  const rows = [...diff.added, ...diff.removed, ...diff.modified]
  return (
    <SectionCard
      title="Changes since last revision"
      icon={GitCompare}
      action={
        <StatusPill tone="text-secondary">
          v{diff.fromVersion} to v{diff.toVersion}
        </StatusPill>
      }
    >
      {rows.length === 0 ? (
        <p className="text-xs text-text-secondary">
          No item changes; {diff.unchanged} item{diff.unchanged === 1 ? '' : 's'} carried
          over unchanged.
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((diffRow) => (
            <DiffRow key={`${diffRow.change}-${diffRow.id}`} diff={diffRow} />
          ))}
        </ul>
      )}
    </SectionCard>
  )
}

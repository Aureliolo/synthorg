import { AlertTriangle, ChevronRight, FileQuestion, ShieldCheck } from 'lucide-react'

import type { PlanItem } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { StatusPill } from '@/components/ui/status-pill'
import { type PlanItemFlag, itemFlags, planItemAnchorId } from '@/utils/plans'

interface FlaggedEntry {
  readonly item: PlanItem
  readonly index: number
  readonly flags: readonly PlanItemFlag[]
}

function collectFlagged(
  items: readonly PlanItem[],
  criticalPath: ReadonlySet<string>,
  roster: ReadonlySet<string> | undefined,
): readonly FlaggedEntry[] {
  return items
    .map((item, index) => ({
      item,
      index,
      flags: itemFlags(item, { onCriticalPath: criticalPath.has(item.id), roster }),
    }))
    .filter((entry) => entry.flags.length > 0)
}

function FlaggedItemRow({ entry }: { entry: FlaggedEntry }) {
  return (
    <li>
      <a
        href={`#${planItemAnchorId(entry.item.id)}`}
        className="flex items-start gap-2 rounded-md border border-border p-2 transition-colors hover:bg-surface focus-visible:bg-surface"
      >
        <div className="min-w-0 flex-1 space-y-1.5">
          <span className="block truncate text-sm font-medium text-foreground">
            {entry.index + 1}. {entry.item.title}
          </span>
          <div className="flex flex-wrap gap-1.5">
            {entry.flags.map((flag) => (
              <StatusPill key={flag.key} tone={flag.tone} ariaLabel={flag.detail}>
                {flag.label}
              </StatusPill>
            ))}
          </div>
        </div>
        <ChevronRight
          className="mt-0.5 size-4 shrink-0 text-muted-foreground"
          aria-hidden="true"
        />
      </a>
    </li>
  )
}

/**
 * The reviewer's worklist: every item carrying a risk or gap that wants a
 * second look, each linking to its card. When nothing is flagged the panel
 * says so plainly rather than vanishing, so a clean plan reads as reviewed.
 *
 * A plan with no items gets its own state. "Nothing flagged" is vacuously
 * true over zero items, and reading it as a clean bill of health next to
 * "0/11 covered" invites a decision on a plan that has not been drafted.
 */
export function PlanAttentionPanel({
  items,
  criticalPath,
  roster,
}: {
  items: readonly PlanItem[]
  criticalPath: ReadonlySet<string>
  roster: ReadonlySet<string> | undefined
}) {
  if (items.length === 0) {
    return (
      <SectionCard title="This plan has no items yet" icon={FileQuestion}>
        <p className="text-sm text-text-secondary">
          Nothing has been drafted to review. Planning either has not finished
          or did not produce a plan.
        </p>
      </SectionCard>
    )
  }
  const flagged = collectFlagged(items, criticalPath, roster)
  if (flagged.length === 0) {
    return (
      <SectionCard title="Nothing flagged for review" icon={ShieldCheck}>
        <p className="text-sm text-text-secondary">
          Every item is owned, scoped, and has acceptance criteria. Read through
          and make your decision.
        </p>
      </SectionCard>
    )
  }
  return (
    <SectionCard
      title="Needs your attention"
      icon={AlertTriangle}
      action={<StatPill label="flagged" value={flagged.length} />}
    >
      <ul className="space-y-2">
        {flagged.map((entry) => (
          <FlaggedItemRow key={entry.item.id} entry={entry} />
        ))}
      </ul>
    </SectionCard>
  )
}

import { AlertTriangle, ChevronRight, FileQuestion, ShieldCheck } from 'lucide-react'

import type { DecompositionProgress, PlanItem, PlanStatus } from '@/api/types/plans'
import { SectionCard } from '@/components/ui/section-card'
import { StatPill } from '@/components/ui/stat-pill'
import { StatusPill } from '@/components/ui/status-pill'
import { planSolicitsReview } from '@/utils/plan-status'
import { buildPlanTree, placedByTree } from '@/utils/planTree'
import { type PlanItemFlag, itemFlags, planItemAnchorId } from '@/utils/plans'

interface FlaggedEntry {
  readonly item: PlanItem
  /** Its position in the tree, so a row names the same thing its card does. */
  readonly label: string
  readonly flags: readonly PlanItemFlag[]
}

function collectFlagged(
  items: readonly PlanItem[],
  criticalPath: ReadonlySet<string>,
  roster: ReadonlySet<string> | undefined,
): readonly FlaggedEntry[] {
  return placedByTree(buildPlanTree(items))
    .map(({ item, label }) => ({
      item,
      label,
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
            {entry.label}. {entry.item.title}
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

interface EmptyCopy {
  readonly title: string
  readonly body: string
}

/**
 * Say how far the decomposition has got, in the operator's terms.
 *
 * The count of items cannot answer this: a recursive decomposition writes its
 * whole tree at the end, so the plan holds zero items for the entire run.
 * These four numbers are what the run itself is bounded by, and without them
 * a working decomposition and a hung one look identical for an hour.
 *
 * @param progress - The latest snapshot the decomposition published.
 * @returns A sentence naming where it is, or `null` before the first report.
 */
function progressLine(progress: DecompositionProgress | null): string | null {
  if (progress === null) return null
  const level =
    progress.deepest_level === 0
      ? 'the first level'
      : `level ${progress.deepest_level + 1}`
  const units =
    progress.units_planned === 1 ? '1 unit' : `${progress.units_planned} units`
  return (
    `Planning is on ${level}, ${units} written so far, ` +
    `${progress.sessions_spent} of ${progress.sessions_limit} planning sessions used.`
  )
}

/**
 * Say WHICH of the two reasons an item-less plan has no items.
 *
 * The plan's own status distinguishes "still being written" from "planning
 * failed" exactly, and asserting both in one sentence left an operator unable
 * to tell whether to wait or to act.
 *
 * The running case used to promise "items appear as they are written", which
 * the product does not do and cannot: the tree is persisted once, at the end.
 * A live run held an operator at zero items for 54 minutes under that
 * sentence. It now says what is true and reports the progress the
 * decomposition publishes, so waiting is an informed choice.
 *
 * @returns The heading and explanation for this plan's empty state.
 */
function emptyCopy(
  status: PlanStatus | undefined,
  progress: DecompositionProgress | null,
): EmptyCopy {
  if (status === 'planning') {
    const line = progressLine(progress)
    return {
      title: 'Planning is still running',
      body:
        line === null
          ? 'The org is drafting this plan. The items are written in one pass at the end, so there is nothing to review until it finishes.'
          : `${line} The items are written in one pass at the end, so there is nothing to review until it finishes.`,
    }
  }
  if (status === 'failed') {
    return {
      title: 'Planning did not produce a plan',
      body: 'Nothing was drafted. The reason is at the top of this page.',
    }
  }
  return {
    title: 'This plan has no items yet',
    body: 'Nothing has been drafted to review.',
  }
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
  status,
  decompositionProgress,
}: {
  items: readonly PlanItem[]
  criticalPath: ReadonlySet<string>
  roster: ReadonlySet<string> | undefined
  status: PlanStatus | undefined
  decompositionProgress: DecompositionProgress | null
}) {
  if (items.length === 0) {
    const copy = emptyCopy(status, decompositionProgress)
    return (
      <SectionCard title={copy.title} icon={FileQuestion}>
        <p className="text-sm text-text-secondary">{copy.body}</p>
      </SectionCard>
    )
  }
  // Past the review decision the same flags are a record of what was weighed,
  // not a request. Asking a second time for input on a plan already running is
  // asking for something the page has no control to accept.
  const solicits = status === undefined || planSolicitsReview(status)
  const flagged = collectFlagged(items, criticalPath, roster)
  if (flagged.length === 0) {
    return (
      <SectionCard
        title={solicits ? 'Nothing flagged for review' : 'Nothing was flagged at review'}
        icon={ShieldCheck}
      >
        <p className="text-sm text-text-secondary">
          Every item is owned, scoped, and has acceptance criteria.
          {solicits && ' Read through and make your decision.'}
        </p>
      </SectionCard>
    )
  }
  return (
    <SectionCard
      title={solicits ? 'Needs your attention' : 'Flagged at review'}
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

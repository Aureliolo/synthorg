import { GitBranch, Package, UserRound } from 'lucide-react'

import type { PlanItem, PlanItemComment } from '@/api/types/plans'
import { cn } from '@/lib/utils'
import { dependencyTitles, planItemAnchorId } from '@/utils/plans'

import { PlanItemComments } from './PlanItemComments'
import {
  ACCENT_HIGHLIGHT,
  AcceptanceCriteria,
  ChipRow,
  DecisionOptions,
  ItemPills,
  UnsplitNote,
} from './PlanItemCard.parts'

/**
 * Spacing steps a subtree is indented per level. Two steps reads as nesting
 * without pushing a level-three card off a narrow viewport.
 */
const SUBTREE_INDENT_STEPS = 4

export interface PlanItemCardProps {
  item: PlanItem
  /** Position in the tree, such as `2.3`. */
  label: string
  /** Levels above this item, which is how far its card is indented. */
  depth: number
  /** Items it was split into; above zero it is an assembly, not work. */
  childCount: number
  onCriticalPath: boolean
  titleById: ReadonlyMap<string, string>
  /**
   * Record a reviewer's option choice on a decision item. Absent (read-only)
   * when the plan is no longer under review, so a decided plan shows the pick
   * without an affordance to change it. Resolves once the write lands so the
   * option button can show its in-flight state.
   */
  onChooseOption?: (itemId: string, optionId: string) => Promise<unknown>
  /** This item's discussion thread (omitted renders no discussion section). */
  comments?: readonly PlanItemComment[]
  /** Post a comment (or a reply) on this item; enables the discussion box. */
  onAddComment?: (
    itemId: string,
    body: string,
    replyToId?: string,
  ) => Promise<PlanItemComment | null>
  className?: string
}

/** Read-only review card for a single plan item, surfacing its review signals. */
export function PlanItemCard({
  item,
  label,
  depth,
  childCount,
  onCriticalPath,
  titleById,
  onChooseOption,
  comments,
  onAddComment,
  className,
}: PlanItemCardProps) {
  const deps = dependencyTitles(item, titleById)
  const headingId = `${planItemAnchorId(item.id)}-heading`
  return (
    <section
      id={planItemAnchorId(item.id)}
      aria-labelledby={headingId}
      // Indented by margin rather than nested markup: every card stays a
      // sibling, so the anchor the attention panel jumps to is reachable at
      // any depth and a collapsed ancestor cannot hide it.
      style={{ marginLeft: `calc(var(--spacing) * ${depth * SUBTREE_INDENT_STEPS})` }}
      className={cn(
        'scroll-mt-4 space-y-3 rounded-md border p-card',
        onCriticalPath ? ACCENT_HIGHLIGHT : 'border-border',
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 id={headingId} className="text-sm font-medium text-foreground">
          {label}. {item.title}
        </h3>
        {item.owner_name !== null && (
          <span className="shrink-0 text-xs text-text-secondary">{item.owner_name}</span>
        )}
      </div>
      <ItemPills item={item} onCriticalPath={onCriticalPath} childCount={childCount} />
      <p className="text-sm text-text-secondary">{item.description}</p>
      <UnsplitNote reason={item.unsplit_reason} />
      <DecisionOptions item={item} onChooseOption={onChooseOption} />
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
      {onAddComment !== undefined && (
        <PlanItemComments
          comments={comments ?? []}
          onSubmit={(body, replyToId) => onAddComment(item.id, body, replyToId)}
        />
      )}
    </section>
  )
}

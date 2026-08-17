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
} from './PlanItemCard.parts'

export interface PlanItemCardProps {
  item: PlanItem
  index: number
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
  index,
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
      className={cn(
        'scroll-mt-4 space-y-3 rounded-md border p-card',
        onCriticalPath ? ACCENT_HIGHLIGHT : 'border-border',
        className,
      )}
    >
      <div className="flex items-baseline justify-between gap-2">
        <h3 id={headingId} className="text-sm font-medium text-foreground">
          {index + 1}. {item.title}
        </h3>
        {item.owner_name !== null && (
          <span className="shrink-0 text-xs text-text-secondary">{item.owner_name}</span>
        )}
      </div>
      <ItemPills item={item} onCriticalPath={onCriticalPath} />
      <p className="text-sm text-text-secondary">{item.description}</p>
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

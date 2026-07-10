import { StatusPill } from '@/components/ui/status-pill'
import {
  getRunOutcomeColor,
  getRunOutcomeIcon,
  getRunOutcomeLabel,
} from '@/utils/approvals'
import type { RunOutcome } from '@/api/types/approvals'

export interface RunOutcomeBadgeProps {
  /** The run outcome to display (succeeded / empty / failed). */
  outcome: RunOutcome
  className?: string
}

/**
 * Failure-aware badge for a task run's outcome. Colour + icon + label so the
 * signal is never carried by colour alone: `succeeded` reads success,
 * `empty` reads a warning ("produced nothing"), and `failed` reads danger.
 * Shared across the approvals queue, the review drawer, and chat prompts so
 * a failed run looks identical everywhere.
 */
export function RunOutcomeBadge({ outcome, className }: RunOutcomeBadgeProps) {
  const label = getRunOutcomeLabel(outcome)
  return (
    <StatusPill
      tone={getRunOutcomeColor(outcome)}
      icon={getRunOutcomeIcon(outcome)}
      ariaLabel={`Run outcome: ${label}`}
      className={className}
    >
      {label}
    </StatusPill>
  )
}

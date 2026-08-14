import type { CompletionOracleVerdict } from '@/api/types/gate-verdicts'
import { cn } from '@/lib/utils'

interface VerdictMapping {
  label: string
  colorClass: string
}

/**
 * Verdict -> label + semantic colour. Table-driven (not an `if` ladder)
 * so the component stays under the complexity cap and the mapping is
 * exhaustive against the `CompletionOracleVerdict` union.
 *
 * Escalate reads as neutral rather than as a failure: nobody decided yet,
 * which is a different thing from a reviewer rejecting the work.
 */
const VERDICT_MAP = {
  approve: {
    label: 'Approved',
    colorClass: 'text-success border-success/20 bg-success/8',
  },
  approve_with_notes: {
    label: 'Approved with notes',
    colorClass: 'text-success border-success/20 bg-success/8',
  },
  reject: {
    label: 'Rejected',
    colorClass: 'text-danger border-danger/20 bg-danger/8',
  },
  escalate: {
    label: 'Escalated',
    colorClass: 'text-warning border-warning/20 bg-warning/8',
  },
} as const satisfies Record<CompletionOracleVerdict, VerdictMapping>

export interface CompletionOracleVerdictBadgeProps {
  verdict: CompletionOracleVerdict
  className?: string
}

/** Semantic badge for a completion-oracle peer-review verdict. */
export function CompletionOracleVerdictBadge({
  verdict,
  className,
}: CompletionOracleVerdictBadgeProps) {
  const { label, colorClass } = VERDICT_MAP[verdict]
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-md border px-2 py-0.5 text-compact font-medium',
        colorClass,
        className,
      )}
    >
      {label}
    </span>
  )
}

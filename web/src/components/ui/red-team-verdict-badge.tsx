import type { RedTeamVerdict } from '@/api/types'
import { cn } from '@/lib/utils'

interface VerdictMapping {
  label: string
  colorClass: string
}

/**
 * Verdict -> label + semantic colour. Table-driven (not an `if` ladder)
 * so the component stays under the complexity cap and the mapping is
 * exhaustive against the `RedTeamVerdict` union.
 */
const VERDICT_MAP = {
  pass: {
    label: 'Passed',
    colorClass: 'text-success border-success/20 bg-success/8',
  },
  pass_with_findings: {
    label: 'Passed with findings',
    colorClass: 'text-warning border-warning/20 bg-warning/8',
  },
  block: {
    label: 'Blocked',
    colorClass: 'text-danger border-danger/20 bg-danger/8',
  },
} as const satisfies Record<RedTeamVerdict, VerdictMapping>

export interface RedTeamVerdictBadgeProps {
  verdict: RedTeamVerdict
  className?: string
}

/** Semantic badge for an adversarial red-team gate verdict. */
export function RedTeamVerdictBadge({ verdict, className }: RedTeamVerdictBadgeProps) {
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

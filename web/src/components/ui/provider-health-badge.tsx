import { cn } from '@/lib/utils'
import type { ProviderHealthStatus } from '@/api/types/providers'
import { getProviderHealthColor } from '@/utils/providers'
import type { SemanticColor } from '@/lib/utils'

const STATUS_LABELS: Record<ProviderHealthStatus, string> = {
  up: 'Up',
  degraded: 'Degraded',
  down: 'Down',
  unknown: 'Unknown',
}

const DOT_COLOR_CLASSES: Record<SemanticColor | 'muted', string> = {
  success: 'bg-success',
  accent: 'bg-accent',
  warning: 'bg-warning',
  danger: 'bg-danger',
  muted: 'bg-text-muted',
}

export interface ProviderHealthBadgeProps {
  status: ProviderHealthStatus
  label?: boolean
  pulse?: boolean
  className?: string
}

/**
 * Provider health status indicator (up/degraded/down colored dot + optional label).
 *
 * Uses the same visual pattern as StatusBadge but with provider-specific health
 * status types and color mapping (up=success, degraded=warning, down=danger).
 */
export function ProviderHealthBadge({
  status,
  label = false,
  pulse = false,
  className,
}: ProviderHealthBadgeProps) {
  const color: SemanticColor | 'muted' = getProviderHealthColor(status)
  const statusLabel = STATUS_LABELS[status]

  return (
    <span
      // ``role="img"`` is implicit-but-fragile for ``<span aria-label>``;
      // browsers expose it inconsistently across screen readers when
      // the label is set but no role is declared. Setting it explicitly
      // makes the label-as-accessible-name contract explicit and matches
      // the documented StatusBadge default in web/CLAUDE.md.
      role="img"
      className={cn('inline-flex items-center gap-1.5', className)}
      aria-label={statusLabel}
    >
      <span
        data-slot="health-dot"
        className={cn(
          'size-1.5 shrink-0 rounded-full',
          DOT_COLOR_CLASSES[color],
          pulse && 'animate-pulse',
        )}
      />
      {label && (
        <span className="text-xs text-text-secondary">{statusLabel}</span>
      )}
    </span>
  )
}

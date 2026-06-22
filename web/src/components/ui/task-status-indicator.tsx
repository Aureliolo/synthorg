import { cn } from '@/lib/utils'
import type { SemanticColor } from '@/utils/agent-status'
import { getTaskStatusColor, getTaskStatusLabel, getPriorityColor, getPriorityLabel } from '@/utils/tasks'
import type { Priority, TaskStatus } from '@/api/types/enums'
import { StatusPill } from './status-pill'

// ── Dot color classes ───────────────────────────────────────

const DOT_COLOR_CLASSES: Record<SemanticColor | 'text-secondary', string> = {
  success: 'bg-success',
  accent: 'bg-accent',
  warning: 'bg-warning',
  danger: 'bg-danger',
  'text-secondary': 'bg-text-secondary',
}

// ── TaskStatusIndicator ─────────────────────────────────────

export interface TaskStatusIndicatorProps {
  status: TaskStatus
  label?: boolean
  pulse?: boolean
  className?: string
  /** Enable live-region announcements for dynamic state changes. Default: false. */
  announce?: boolean
}

export function TaskStatusIndicator({ status, label = false, pulse = false, className, announce = false }: TaskStatusIndicatorProps) {
  const color = getTaskStatusColor(status)
  const statusLabel = getTaskStatusLabel(status)

  return (
    <span
      className={cn('inline-flex items-center gap-1.5', className)}
      aria-label={statusLabel}
      role={announce ? 'status' : undefined}
      aria-live={announce ? 'polite' : undefined}
    >
      <span
        data-slot="status-dot"
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

// ── PriorityBadge ───────────────────────────────────────────

export interface PriorityBadgeProps {
  priority: Priority
  className?: string
}

export function PriorityBadge({ priority, className }: PriorityBadgeProps) {
  const color = getPriorityColor(priority)
  const label = getPriorityLabel(priority)

  return (
    <StatusPill tone={color} ariaLabel={`Priority: ${label}`} className={className}>
      {label}
    </StatusPill>
  )
}

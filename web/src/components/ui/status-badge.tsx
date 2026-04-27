import { motion } from 'motion/react'
import { cn, type AgentRuntimeStatus, type SemanticColor, getStatusColor } from '@/lib/utils'
import { useStatusTransition } from '@/hooks/useStatusTransition'

const STATUS_LABELS: Record<AgentRuntimeStatus, string> = {
  active: 'Active',
  idle: 'Idle',
  error: 'Error',
  offline: 'Offline',
}

const DOT_COLOR_CLASSES: Record<SemanticColor | 'text-secondary', string> = {
  success: 'bg-success',
  accent: 'bg-accent',
  warning: 'bg-warning',
  danger: 'bg-danger',
  'text-secondary': 'bg-text-secondary',
}

export interface StatusBadgeProps {
  status: AgentRuntimeStatus
  label?: boolean
  pulse?: boolean
  className?: string
  /**
   * Override classes merged into the inner dot. Use for callers that need
   * a non-default dot size or ring (e.g. dense org-chart cards where the
   * default `size-1.5` is too small to read against the surface). Color
   * still derives from `getStatusColor(status)`.
   */
  dotClassName?: string
  /**
   * Override the wrapper's accessible name. Defaults to the status label
   * (e.g. "Active"). Use when a row needs row-scoped context such as the
   * agent ID alongside the status.
   */
  ariaLabel?: string
  /** Enable animated color transition on status change. Default: false. */
  animated?: boolean
  /** Enable live-region announcements for dynamic state changes. Default: false. */
  announce?: boolean
  /**
   * When true, treat this badge as decoration layered beside an already
   * labeled sibling (e.g. inside `AgentNode` where the agent name is
   * displayed adjacent to the badge). The wrapper becomes
   * `aria-hidden` so screen readers do not announce redundant status
   * text. Default: false.
   */
  decorative?: boolean
}

export function StatusBadge({
  status,
  label = false,
  pulse = false,
  className,
  dotClassName,
  ariaLabel,
  animated = false,
  announce = false,
  decorative = false,
}: StatusBadgeProps) {
  const color = getStatusColor(status)
  const statusLabel = STATUS_LABELS[status]
  const { motionProps } = useStatusTransition(status)

  // Decorative mode: the caller already names the status; hide the dot
  // tree entirely from screen readers.
  if (decorative) {
    return (
      <span
        className={cn('inline-flex items-center gap-1.5', className)}
        aria-hidden="true"
      >
        {animated ? (
          <motion.span
            data-slot="status-dot"
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              DOT_COLOR_CLASSES[color],
              pulse && 'animate-pulse',
              dotClassName,
            )}
            {...motionProps}
          />
        ) : (
          <span
            data-slot="status-dot"
            className={cn(
              'size-1.5 shrink-0 rounded-full',
              DOT_COLOR_CLASSES[color],
              pulse && 'animate-pulse',
              dotClassName,
            )}
          />
        )}
        {label && (
          <span className="text-xs text-text-secondary">{statusLabel}</span>
        )}
      </span>
    )
  }

  return (
    <span
      role={announce ? 'status' : 'img'}
      aria-label={ariaLabel ?? statusLabel}
      aria-live={announce ? 'polite' : undefined}
      className={cn('inline-flex items-center gap-1.5', className)}
    >
      {animated ? (
        <motion.span
          data-slot="status-dot"
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            DOT_COLOR_CLASSES[color],
            pulse && 'animate-pulse',
            dotClassName,
          )}
          {...motionProps}
        />
      ) : (
        <span
          data-slot="status-dot"
          className={cn(
            'size-1.5 shrink-0 rounded-full',
            DOT_COLOR_CLASSES[color],
            pulse && 'animate-pulse',
            dotClassName,
          )}
        />
      )}
      {label && (
        <span className="text-xs text-text-secondary">{statusLabel}</span>
      )}
    </span>
  )
}

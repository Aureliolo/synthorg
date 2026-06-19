import { motion } from 'motion/react'
import { cn } from '@/lib/utils'
import { getStatusColor, type AgentRuntimeStatus, type SemanticColor } from '@/utils/agent-status'
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

function StatusDot({
  color,
  pulse,
  dotClassName,
  animated,
  motionProps,
}: {
  color: SemanticColor | 'text-secondary'
  pulse: boolean
  dotClassName: string | undefined
  animated: boolean
  motionProps: ReturnType<typeof useStatusTransition>['motionProps']
}) {
  const dotClass = cn(
    'size-1.5 shrink-0 rounded-full',
    DOT_COLOR_CLASSES[color],
    pulse && 'animate-pulse',
    dotClassName,
  )
  if (animated) {
    return <motion.span data-slot="status-dot" className={dotClass} {...motionProps} />
  }
  return <span data-slot="status-dot" className={dotClass} />
}

interface BadgeWrapperProps {
  decorative: boolean
  announce: boolean
  ariaLabel: string | undefined
  statusLabel: string
  className: string | undefined
  children: React.ReactNode
}

function BadgeWrapper({
  decorative,
  announce,
  ariaLabel,
  statusLabel,
  className,
  children,
}: BadgeWrapperProps) {
  const baseClass = cn('inline-flex items-center gap-1.5', className)
  if (decorative) {
    return <span className={baseClass} aria-hidden="true">{children}</span>
  }
  return (
    <span
      role={announce ? 'status' : 'img'}
      aria-label={ariaLabel ?? statusLabel}
      aria-live={announce ? 'polite' : undefined}
      className={baseClass}
    >
      {children}
    </span>
  )
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
  return (
    <BadgeWrapper
      decorative={decorative}
      announce={announce}
      ariaLabel={ariaLabel}
      statusLabel={statusLabel}
      className={className}
    >
      <StatusDot
        color={color}
        pulse={pulse}
        dotClassName={dotClassName}
        animated={animated}
        motionProps={motionProps}
      />
      {label && <span className="text-xs text-text-secondary">{statusLabel}</span>}
    </BadgeWrapper>
  )
}

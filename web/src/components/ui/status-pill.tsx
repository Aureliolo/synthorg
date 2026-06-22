import { createElement, type ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { SemanticColor } from '@/utils/agent-status'

export type StatusPillTone = SemanticColor | 'text-secondary'

/**
 * Canonical colour vocabulary for inline status pills. Mirrors the prior
 * per-component `BADGE_COLOR_CLASSES` maps so every pill (priority, approval
 * flags, meeting status, ...) shares one palette.
 */
const PILL_TONE_CLASSES: Record<StatusPillTone, string> = {
  danger: 'border-danger/30 bg-danger/10 text-danger',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  accent: 'border-accent/30 bg-accent/10 text-accent',
  success: 'border-success/30 bg-success/10 text-success',
  'text-secondary': 'border-border bg-surface text-text-secondary',
}

export interface StatusPillProps {
  /** Semantic tone mapped to the shared palette. Mutually exclusive with `toneClassName`. */
  tone?: StatusPillTone | undefined
  /** Raw colour classes for feature-specific palettes (e.g. meeting status). */
  toneClassName?: string | undefined
  /** Optional leading icon. */
  icon?: LucideIcon | undefined
  children: ReactNode
  className?: string | undefined
  /** Accessible label override when the visible text is not self-describing. */
  ariaLabel?: string | undefined
}

/**
 * The single inline status-pill primitive: one corner radius (`rounded-full`),
 * one font token (`text-micro`), one padding (`px-2 py-0.5`) across the
 * dashboard. Pass `tone` for the shared palette or `toneClassName` for a
 * feature-specific colour set.
 */
export function StatusPill({
  tone,
  toneClassName,
  icon,
  children,
  className,
  ariaLabel,
}: StatusPillProps) {
  return (
    <span
      aria-label={ariaLabel}
      className={cn(
        'inline-flex shrink-0 items-center gap-1 rounded-full border px-2 py-0.5 text-micro font-medium leading-none',
        tone !== undefined ? PILL_TONE_CLASSES[tone] : toneClassName,
        className,
      )}
    >
      {icon !== undefined && createElement(icon, { className: 'size-3', 'aria-hidden': true })}
      {children}
    </span>
  )
}

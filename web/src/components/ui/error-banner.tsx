import type { LucideIcon } from 'lucide-react'
import { AlertTriangle, Info, WifiOff, X, AlertCircle } from 'lucide-react'
import { isValidElement, useEffect, useState } from 'react'
import { cn } from '@/lib/utils'
import { Button } from './button'

export type ErrorBannerSeverity = 'error' | 'warning' | 'info'
export type ErrorBannerVariant = 'inline' | 'section' | 'offline'

interface ErrorBannerAction {
  label: string
  onClick: () => void
}

function isActionObject(value: unknown): value is ErrorBannerAction {
  if (typeof value !== 'object' || value === null) return false
  if (isValidElement(value)) return false
  const candidate = value as { label?: unknown; onClick?: unknown }
  return typeof candidate.label === 'string' && typeof candidate.onClick === 'function'
}

export interface ErrorBannerProps {
  /** Layout density. `section` is the default page-level banner; `inline` is compact for form rows/cards; `offline` is the connectivity variant. */
  variant?: ErrorBannerVariant
  /** Color + ARIA role mapping. `error` uses role=alert, `warning`/`info` use role=status. Ignored when variant='offline' (forces warning). */
  severity?: ErrorBannerSeverity
  title: string
  description?: string | React.ReactNode
  /** When provided, renders a Retry button that invokes this handler. */
  onRetry?: () => void
  /**
   * When set, the Retry button is disabled and shows a live countdown
   * (``Retry in 12s``) until the cooldown expires. Pass the seconds
   * value parsed from a server ``Retry-After`` header (or
   * ``ErrorDetail.retry_after``); the banner re-enables Retry when
   * the countdown reaches zero. The countdown is cosmetic only -- the
   * caller still owns the actual retry decision via ``onRetry``.
   */
  retryAfterSeconds?: number | null
  /** When provided, renders a Dismiss (X) button that invokes this handler. */
  onDismiss?: () => void
  /** Override the default icon (by severity). Always rendered at h-4 w-4 for consistency. */
  icon?: LucideIcon
  /** Optional action label shown next to Retry (e.g. "Learn more" link). */
  action?: ErrorBannerAction | React.ReactNode
  className?: string
}

const SEVERITY_ICON: Record<ErrorBannerSeverity, LucideIcon> = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
}

const SEVERITY_STYLES: Record<ErrorBannerSeverity, string> = {
  error: 'border-danger/30 bg-danger/5 text-danger',
  warning: 'border-warning/30 bg-warning/5 text-warning',
  info: 'border-accent/30 bg-accent/5 text-accent',
}

/**
 * Shared error / warning / info banner for list fetch failures, offline
 * state, onboarding retry guidance, and form-level errors.
 *
 * For mutation errors use the toast store; for unrecoverable render errors
 * use `ErrorBoundary` with `level='section'`. See web/CLAUDE.md for the
 * full error-surface policy.
 */
export function ErrorBanner({
  variant = 'section',
  severity: severityProp = 'error',
  title,
  description,
  onRetry,
  retryAfterSeconds,
  onDismiss,
  icon,
  action,
  className,
}: ErrorBannerProps) {
  const severity: ErrorBannerSeverity = variant === 'offline' ? 'warning' : severityProp
  const Icon = icon ?? (variant === 'offline' ? WifiOff : SEVERITY_ICON[severity])

  const role = severity === 'error' ? 'alert' : 'status'
  const ariaLive = severity === 'error' ? 'assertive' : 'polite'

  const densityClasses = variant === 'inline' ? 'gap-2 p-card text-xs' : 'gap-3 p-card text-sm'

  // Live countdown for Retry-After cooldowns. ``remaining`` is seeded
  // from the latest ``retryAfterSeconds`` prop via render-phase deriv-
  // ation (no synchronous ``setRemaining`` inside the effect, which
  // ESLint's ``set-state-in-effect`` rule rightly flags as a render-
  // loop hazard). The effect owns only the ``setInterval`` that ticks
  // the value down once per second; ``clearInterval`` runs when the
  // prop changes or the component unmounts.
  // Reject ``Infinity`` and ``NaN``: either would lock the Retry button
  // forever (``Infinity > 0`` is true and never decrements; ``Math.ceil(NaN)``
  // is ``NaN`` which fails every ``<= 1`` comparison).
  const initialRemaining =
    typeof retryAfterSeconds === 'number' &&
    Number.isFinite(retryAfterSeconds) &&
    retryAfterSeconds > 0
      ? Math.ceil(retryAfterSeconds)
      : null
  const [remaining, setRemaining] = useState<number | null>(initialRemaining)
  // Track the prop key we last seeded from so a fresh ``retryAfterSeconds``
  // prop resets the countdown without firing ``setRemaining`` in the
  // effect body.
  const [seedKey, setSeedKey] = useState<number | null>(retryAfterSeconds ?? null)
  if (seedKey !== (retryAfterSeconds ?? null)) {
    setSeedKey(retryAfterSeconds ?? null)
    setRemaining(initialRemaining)
  }
  useEffect(() => {
    if (
      typeof retryAfterSeconds !== 'number' ||
      !Number.isFinite(retryAfterSeconds) ||
      retryAfterSeconds <= 0
    )
      return
    const id = setInterval(() => {
      setRemaining((prev) => {
        if (prev === null || prev <= 1) {
          clearInterval(id)
          return null
        }
        return prev - 1
      })
    }, 1000)
    return () => clearInterval(id)
  }, [retryAfterSeconds])
  const retryDisabled = remaining !== null && remaining > 0
  const retryLabel = retryDisabled ? `Retry in ${remaining}s` : 'Retry'

  return (
    <div
      role={role}
      aria-live={ariaLive}
      className={cn(
        'flex items-start rounded-lg border',
        SEVERITY_STYLES[severity],
        densityClasses,
        className,
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" aria-hidden="true" strokeWidth={1.75} />

      <div className="min-w-0 flex-1">
        <p className={cn('font-medium', variant === 'inline' ? 'text-xs' : 'text-sm')}>
          {title}
        </p>
        {description !== undefined && description !== null && (
          typeof description === 'string' ? (
            <p className={cn('mt-1 text-xs text-muted-foreground')}>
              {description}
            </p>
          ) : (
            <div className={cn('mt-1 text-xs text-muted-foreground')}>
              {description}
            </div>
          )
        )}
        {(onRetry || action) && (
          <div className="mt-2 flex flex-wrap gap-2">
            {onRetry && (
              <Button
                size="xs"
                variant="outline"
                onClick={onRetry}
                disabled={retryDisabled}
                aria-live={retryDisabled ? 'polite' : undefined}
              >
                {retryLabel}
              </Button>
            )}
            {action && (isActionObject(action) ? (
              <Button size="xs" variant="ghost" onClick={action.onClick}>
                {action.label}
              </Button>
            ) : action)}
          </div>
        )}
      </div>

      {onDismiss && (
        <Button
          size="icon-xs"
          variant="ghost"
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 -mt-0.5 -mr-1"
        >
          <X className="size-3" aria-hidden="true" />
        </Button>
      )}
    </div>
  )
}

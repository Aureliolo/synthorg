import type { LucideIcon } from 'lucide-react'
import { AlertTriangle, Copy, Info, WifiOff, X, AlertCircle } from 'lucide-react'
import { isValidElement, useEffect, useRef, useState } from 'react'
import { createLogger } from '@/lib/logger'
import { cn } from '@/lib/utils'
import { useToastStore } from '@/stores/toast'
import { Button } from './button'

const log = createLogger('error-banner')

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
  /**
   * Optional token that re-arms the countdown when the value changes,
   * even if ``retryAfterSeconds`` is unchanged. Pass a fresh value
   * (e.g. an incrementing error counter or the timestamp of the
   * latest 429) when the caller wants a new error with the same
   * ``retry_after`` to restart the cooldown rather than leave the
   * Retry button enabled after the previous countdown reached zero.
   */
  retryResetToken?: string | number | null
  /** When provided, renders a Dismiss (X) button that invokes this handler. */
  onDismiss?: () => void
  /** Override the default icon (by severity). Always rendered at h-4 w-4 for consistency. */
  icon?: LucideIcon
  /** Optional action label shown next to Retry (e.g. "Learn more" link). */
  action?: ErrorBannerAction | React.ReactNode
  /**
   * RFC 9457 ``instance`` correlation ID from the server's error
   * response. When present, the banner renders a compact copy chip
   * next to the action row so operators can paste the ID into a
   * support ticket. Use `ApiRequestError.correlationId` to source it.
   */
  correlationId?: string | null
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
  retryResetToken,
  onDismiss,
  icon,
  action,
  correlationId,
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
  // Compute an absolute deadline (ms since epoch) per cooldown so the
  // displayed remaining is always derived from wall-clock time rather
  // than ``prev - 1`` per tick. Browsers throttle ``setInterval`` in
  // backgrounded tabs (typically to 1 Hz max, more aggressively under
  // load), so a decrement-per-tick countdown drifts behind real time
  // and can keep the Retry button disabled past the actual cooldown
  // expiry. Recomputing from the deadline keeps the timer correct even
  // after the tab returns to the foreground.
  const isValidCooldown =
    typeof retryAfterSeconds === 'number' &&
    Number.isFinite(retryAfterSeconds) &&
    retryAfterSeconds > 0
  const initialRemaining = isValidCooldown ? Math.ceil(retryAfterSeconds) : null
  const [remaining, setRemaining] = useState<number | null>(initialRemaining)
  // Track ``retryAfterSeconds`` AND ``retryResetToken`` so a fresh
  // 429 with the SAME duration but a NEW token (e.g. a different error
  // instance) restarts the countdown -- without the token, an
  // identical-duration follow-up would silently leave Retry enabled
  // because the countdown ran to zero on the previous error.
  // ``useRef`` (not ``useState``) for the previous-signature sentinel:
  // we don't need to trigger a render when the signature changes (we
  // reseed ``remaining`` directly), and ``useRef`` avoids the extra
  // render-phase state update + commit that ``useState`` would force.
  const seedSignature = `${retryAfterSeconds ?? ''}|${retryResetToken ?? ''}`
  const prevSeedRef = useRef<string>(seedSignature)
  if (prevSeedRef.current !== seedSignature) {
    prevSeedRef.current = seedSignature
    setRemaining(initialRemaining)
  }
  useEffect(() => {
    if (!isValidCooldown) return
    // Compute the absolute expiry once per cooldown inside the effect
    // (calling ``Date.now()`` during render violates React's purity
    // rules; effects are the canonical place for "now"). Each tick
    // recomputes ``remaining`` from the deadline so a backgrounded
    // tab whose ``setInterval`` was throttled catches up to wall-clock
    // time on the next tick instead of drifting behind by N seconds.
    const deadline = Date.now() + retryAfterSeconds * 1000
    const id = setInterval(() => {
      const next = Math.max(0, Math.ceil((deadline - Date.now()) / 1000))
      if (next <= 0) {
        clearInterval(id)
        setRemaining(null)
        return
      }
      setRemaining(next)
    }, 1000)
    return () => clearInterval(id)
    // ``seedSignature`` covers ``retryResetToken`` changes too: when a
    // fresh error arrives with the same ``retryAfterSeconds`` but a new
    // token, render-phase reseed sets ``remaining`` back to the initial
    // value AND this effect fires to compute a fresh deadline + start
    // a new interval (without it the previous interval -- already
    // cleared at zero -- would not restart, leaving the disabled-Retry
    // state stuck).
  }, [retryAfterSeconds, seedSignature, isValidCooldown])
  const retryDisabled = remaining !== null && remaining > 0

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
        {Boolean(onRetry ?? action ?? correlationId) && (
          <div className="mt-2 flex flex-wrap items-center gap-2">
            {onRetry && (
              <div className="inline-flex items-center gap-2">
                <Button
                  size="xs"
                  variant="outline"
                  onClick={onRetry}
                  disabled={retryDisabled}
                >
                  Retry
                </Button>
                {retryDisabled && (
                  /*
                   * Countdown text rendered as a separate ``aria-hidden``
                   * sibling so the per-second ticks don't mutate the
                   * Retry button's accessible name (the previous design
                   * caused screen readers to re-announce ``Retry in 12s``
                   * every second). Sighted users still see the timer; the
                   * button's disabled state is what assistive tech
                   * conveys, and re-enabling fires a single state change
                   * announcement instead of N per-second updates.
                   */
                  <span
                    aria-hidden="true"
                    className="font-mono text-compact text-muted-foreground"
                  >
                    Retry in {remaining}s
                  </span>
                )}
              </div>
            )}
            {action !== undefined && (isActionObject(action) ? (
              <Button size="xs" variant="ghost" onClick={action.onClick}>
                {action.label}
              </Button>
            ) : action)}
            {correlationId && (
              <CorrelationIdChip correlationId={correlationId} />
            )}
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

interface CorrelationIdChipProps {
  correlationId: string
}

const CORRELATION_ID_DISPLAY_LENGTH = 8

function CorrelationIdChip({ correlationId }: CorrelationIdChipProps) {
  const toast = useToastStore((s) => s.add)
  const displayId =
    correlationId.length > CORRELATION_ID_DISPLAY_LENGTH
      ? `${correlationId.slice(0, CORRELATION_ID_DISPLAY_LENGTH)}…`
      : correlationId

  const handleCopy = async () => {
    if (typeof navigator === 'undefined' || !navigator.clipboard) {
      log.warn('Clipboard API not available; correlation ID copy skipped')
      return
    }
    try {
      await navigator.clipboard.writeText(correlationId)
      toast({
        variant: 'success',
        title: 'Correlation ID copied',
        description: correlationId,
      })
    } catch (err) {
      log.warn('Correlation ID copy failed', err)
    }
  }

  return (
    <Button
      size="xs"
      variant="ghost"
      onClick={() => void handleCopy()}
      title={`Copy correlation ID: ${correlationId}`}
      aria-label={`Copy correlation ID ${correlationId}`}
      className="gap-1 font-mono text-compact text-muted-foreground"
    >
      <Copy className="size-3" aria-hidden="true" />
      {displayId}
    </Button>
  )
}

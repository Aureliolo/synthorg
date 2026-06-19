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
  variant?: ErrorBannerVariant | undefined
  /** Color + ARIA role mapping. `error` uses role=alert, `warning`/`info` use role=status. Ignored when variant='offline' (forces warning). */
  severity?: ErrorBannerSeverity | undefined
  title: string
  description?: string | React.ReactNode
  /** When provided, renders a Retry button that invokes this handler. */
  onRetry?: (() => void) | undefined
  /**
   * When set, the Retry button is disabled and shows a live countdown
   * (``Retry in 12s``) until the cooldown expires. Pass the seconds
   * value parsed from a server ``Retry-After`` header (or
   * ``ErrorDetail.retry_after``); the banner re-enables Retry when
   * the countdown reaches zero. The countdown is cosmetic only: the
   * caller still owns the actual retry decision via ``onRetry``.
   */
  retryAfterSeconds?: number | null | undefined
  /**
   * Optional token that re-arms the countdown when the value changes,
   * even if ``retryAfterSeconds`` is unchanged. Pass a fresh value
   * (e.g. an incrementing error counter or the timestamp of the
   * latest 429) when the caller wants a new error with the same
   * ``retry_after`` to restart the cooldown rather than leave the
   * Retry button enabled after the previous countdown reached zero.
   */
  retryResetToken?: string | number | null | undefined
  /** When provided, renders a Dismiss (X) button that invokes this handler. */
  onDismiss?: (() => void) | undefined
  /** Override the default icon (by severity). Always rendered at h-4 w-4 for consistency. */
  icon?: LucideIcon | undefined
  /** Optional action label shown next to Retry (e.g. "Learn more" link). */
  action?: ErrorBannerAction | React.ReactNode
  /**
   * RFC 9457 ``instance`` correlation ID from the server's error
   * response. When present, the banner renders a compact copy chip
   * next to the action row so operators can paste the ID into a
   * support ticket. Use `ApiRequestError.correlationId` to source it.
   */
  correlationId?: string | null | undefined
  className?: string | undefined
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

function _isValidCooldown(seconds: number | null | undefined): seconds is number {
  return typeof seconds === 'number' && Number.isFinite(seconds) && seconds > 0
}

/**
 * Live countdown for Retry-After cooldowns. Seeded from
 * ``retryAfterSeconds`` via render-phase derivation (no synchronous
 * ``setRemaining`` inside the effect, which ESLint's
 * ``set-state-in-effect`` rule rightly flags as a render-loop hazard).
 * The effect owns only the ``setInterval`` that ticks the value down
 * once per second; ``clearInterval`` runs when the prop changes or
 * the component unmounts.
 *
 * Computes an absolute deadline (ms since epoch) per cooldown so the
 * displayed remaining is always derived from wall-clock time rather
 * than ``prev - 1`` per tick. Browsers throttle ``setInterval`` in
 * backgrounded tabs (typically to 1 Hz max, more aggressively under
 * load), so a decrement-per-tick countdown drifts behind real time
 * and can keep the Retry button disabled past the actual cooldown
 * expiry. Recomputing from the deadline keeps the timer correct even
 * after the tab returns to the foreground.
 */
function useRetryCountdown(
  retryAfterSeconds: number | null | undefined,
  retryResetToken: string | number | null | undefined,
): number | null {
  const isValidCooldown = _isValidCooldown(retryAfterSeconds)
  const initialRemaining = isValidCooldown ? Math.ceil(retryAfterSeconds) : null
  const [remaining, setRemaining] = useState<number | null>(initialRemaining)
  // Track ``retryAfterSeconds`` AND ``retryResetToken`` so a fresh 429
  // with the SAME duration but a NEW token (e.g. a different error
  // instance) restarts the countdown. Without the token an identical-
  // duration follow-up would silently leave Retry enabled because the
  // countdown ran to zero on the previous error.
  const seedSignature = `${retryAfterSeconds ?? ''}|${retryResetToken ?? ''}`
  const prevSeedRef = useRef<string>(seedSignature)
  if (prevSeedRef.current !== seedSignature) {
    prevSeedRef.current = seedSignature
    setRemaining(initialRemaining)
  }
  useEffect(() => {
    if (!isValidCooldown) return
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
  }, [retryAfterSeconds, seedSignature, isValidCooldown])
  return remaining
}

function ErrorBannerRetry({
  onRetry,
  remaining,
}: {
  onRetry: () => void
  remaining: number | null
}) {
  const disabled = remaining !== null && remaining > 0
  return (
    <div className="inline-flex items-center gap-2">
      <Button size="xs" variant="outline" onClick={onRetry} disabled={disabled}>
        Retry
      </Button>
      {disabled && (
        // Countdown text rendered as a separate ``aria-hidden`` sibling
        // so the per-second ticks don't mutate the Retry button's
        // accessible name (the previous design caused screen readers
        // to re-announce "Retry in 12s" every second). Sighted users
        // still see the timer; the button's disabled state is what
        // assistive tech conveys, and re-enabling fires a single
        // state-change announcement instead of N per-second updates.
        <span aria-hidden="true" className="font-mono text-compact text-muted-foreground">
          Retry in {remaining}s
        </span>
      )}
    </div>
  )
}

function ErrorBannerActionSlot({ action }: { action: ErrorBannerAction | React.ReactNode }) {
  if (action == null || action === false) return null
  if (isActionObject(action)) {
    return (
      <Button size="xs" variant="ghost" onClick={action.onClick}>
        {action.label}
      </Button>
    )
  }
  return <>{action}</>
}

function ErrorBannerDescription({
  description,
}: {
  description: string | React.ReactNode | undefined
}) {
  if (description === undefined || description === null) return null
  if (typeof description === 'string') {
    return <p className="mt-1 text-xs text-muted-foreground">{description}</p>
  }
  return <div className="mt-1 text-xs text-muted-foreground">{description}</div>
}

function _normalizeCorrelationId(value: string | null | undefined): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed.length > 0 ? trimmed : null
}

interface BannerPresentation {
  readonly severity: ErrorBannerSeverity
  readonly Icon: LucideIcon
  readonly role: 'alert' | 'status'
  readonly ariaLive: 'assertive' | 'polite'
  readonly densityClasses: string
  readonly titleClass: string
}

function _resolvePresentation(
  variant: ErrorBannerVariant,
  severityProp: ErrorBannerSeverity,
  iconOverride: LucideIcon | undefined,
): BannerPresentation {
  const severity = variant === 'offline' ? 'warning' : severityProp
  const Icon = iconOverride ?? (variant === 'offline' ? WifiOff : SEVERITY_ICON[severity])
  return {
    severity,
    Icon,
    role: severity === 'error' ? 'alert' : 'status',
    ariaLive: severity === 'error' ? 'assertive' : 'polite',
    densityClasses: variant === 'inline' ? 'gap-2 p-card text-xs' : 'gap-3 p-card text-sm',
    titleClass: variant === 'inline' ? 'text-xs' : 'text-sm',
  }
}

function ErrorBannerActions({
  onRetry,
  remaining,
  action,
  correlationId,
}: {
  onRetry: (() => void) | undefined
  remaining: number | null
  action: ErrorBannerAction | React.ReactNode
  correlationId: string | null
}) {
  const hasAction = action != null && action !== false
  const hasAny = onRetry != null || hasAction || correlationId !== null
  if (!hasAny) return null
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      {onRetry && <ErrorBannerRetry onRetry={onRetry} remaining={remaining} />}
      <ErrorBannerActionSlot action={action} />
      {correlationId !== null && <CorrelationIdChip correlationId={correlationId} />}
    </div>
  )
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
  const presentation = _resolvePresentation(variant, severityProp, icon)
  const remaining = useRetryCountdown(retryAfterSeconds, retryResetToken)
  // Normalise once: an empty / whitespace-only ``correlationId`` would
  // pass the ``!= null`` predicate (rendering the action row with
  // ``mt-2`` spacing) but fail the truthy guard at the chip render
  // site, leaving a visible empty row. Treat any blank as "absent" so
  // the row only appears when there is a real chip to draw.
  const normalizedCorrelationId = _normalizeCorrelationId(correlationId)

  return (
    <div
      role={presentation.role}
      aria-live={presentation.ariaLive}
      className={cn(
        'flex items-start rounded-lg border',
        SEVERITY_STYLES[presentation.severity],
        presentation.densityClasses,
        className,
      )}
    >
      <presentation.Icon
        className="mt-0.5 size-4 shrink-0"
        aria-hidden="true"
        strokeWidth={1.75}
      />
      <div className="min-w-0 flex-1">
        <p className={cn('font-medium', presentation.titleClass)}>{title}</p>
        <ErrorBannerDescription description={description} />
        <ErrorBannerActions
          onRetry={onRetry}
          remaining={remaining}
          action={action}
          correlationId={normalizedCorrelationId}
        />
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

export interface CorrelationIdChipProps {
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
    // ``navigator.clipboard`` is typed non-null by lib.dom but is undefined in
    // insecure contexts / older browsers, so widen it to reflect runtime
    // reality before the feature-detection guard.
    const clipboard: Clipboard | undefined =
      typeof navigator === 'undefined' ? undefined : navigator.clipboard
    if (!clipboard) {
      log.warn('Clipboard API not available; correlation ID copy skipped')
      toast({
        variant: 'error',
        title: 'Could not copy correlation ID',
        description: 'Clipboard access is unavailable in this context.',
      })
      return
    }
    try {
      await clipboard.writeText(correlationId)
      toast({
        variant: 'success',
        title: 'Correlation ID copied',
        description: correlationId,
      })
    } catch (err) {
      log.warn('Correlation ID copy failed', err)
      toast({
        variant: 'error',
        title: 'Could not copy correlation ID',
        description: 'Check your clipboard permissions and try again.',
      })
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

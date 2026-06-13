import { memo, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Clock, ShieldOff, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useFlash } from '@/hooks/useFlash'
import {
  DOT_COLOR_CLASSES,
  URGENCY_BADGE_CLASSES,
  formatUrgency,
  getRiskLevelColor,
  getRiskLevelLabel,
  getUrgencyColor,
} from '@/utils/approvals'
import type { ApprovalResponse } from '@/api/types/approvals'

export interface ApprovalCardProps {
  approval: ApprovalResponse
  selected: boolean
  onSelect: (id: string) => void
  onApprove: (id: string) => void
  onReject: (id: string) => void
  onToggleSelect: (id: string) => void
  className?: string
}

function isLowConfidence(approval: ApprovalResponse): boolean {
  if (approval.metadata['low_confidence'] === 'true') return true
  const raw = approval.metadata['confidence_score']
  const score = raw != null ? parseFloat(raw) : NaN
  return !Number.isNaN(score) && score < 0.5
}

/** Flash the card briefly when the approval's status changes. */
function useStatusFlash(status: string): ReturnType<typeof useFlash>['flashStyle'] {
  const { flashStyle, triggerFlash } = useFlash()
  const prevStatusRef = useRef(status)
  useEffect(() => {
    if (status !== prevStatusRef.current) {
      triggerFlash()
      prevStatusRef.current = status
    }
  }, [status, triggerFlash])
  return flashStyle
}

/**
 * Local 1s countdown over `seconds_remaining`. Reset + interval-restart
 * are collapsed into one prop-keyed effect so a WS refresh restarts on a
 * clean cadence; the `cancelled` flag guards both the microtask and the
 * tick so a freshly-set countdown is never decremented by a stale tick.
 * A sibling effect stops the timer once the countdown reaches zero so an
 * expired card mounted in the paginated queue does not keep a dormant
 * 1-Hz interval running.
 */
function useApprovalCountdown(secondsRemaining: number | null, isPending: boolean): number | null {
  const [countdown, setCountdown] = useState(secondsRemaining)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (!cancelled) setCountdown(secondsRemaining)
    })
    if (!isPending || secondsRemaining === null || secondsRemaining <= 0) {
      return () => {
        cancelled = true
      }
    }
    timerRef.current = setInterval(() => {
      if (cancelled) return
      setCountdown((prev) => (prev === null || prev <= 1 ? 0 : prev - 1))
    }, 1000)
    return () => {
      cancelled = true
      if (timerRef.current !== null) {
        clearInterval(timerRef.current)
        timerRef.current = null
      }
    }
  }, [secondsRemaining, isPending])

  useEffect(() => {
    if (countdown !== null && countdown <= 0 && timerRef.current !== null) {
      clearInterval(timerRef.current)
      timerRef.current = null
    }
  }, [countdown])

  return countdown
}

interface ApprovalBadgesProps {
  isPending: boolean
  countdown: number | null
  urgencyColor: ReturnType<typeof getUrgencyColor>
  isBlocked: boolean
  isSuspicious: boolean
  showLowConfidence: boolean
}

function ApprovalBadges({
  isPending,
  countdown,
  urgencyColor,
  isBlocked,
  isSuspicious,
  showLowConfidence,
}: ApprovalBadgesProps) {
  return (
    <>
      {isPending && countdown !== null && (
        <span
          className={cn(
            'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 font-mono text-[11px] font-medium shrink-0',
            URGENCY_BADGE_CLASSES[urgencyColor],
          )}
          aria-label={`Expires in ${formatUrgency(countdown)}`}
        >
          <Clock className="size-3" aria-hidden="true" />
          <span aria-hidden="true">{formatUrgency(countdown)}</span>
        </span>
      )}
      {isPending && countdown === null && (
        <span className="text-[11px] text-muted-foreground shrink-0">No expiry</span>
      )}
      {isBlocked && (
        <span
          className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium shrink-0 border-danger/30 bg-danger/10 text-danger"
          aria-label="Blocked by safety classifier"
        >
          <ShieldOff className="size-3" aria-hidden="true" />
          Blocked
        </span>
      )}
      {isSuspicious && (
        <span
          className="inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium shrink-0 border-warning/30 bg-warning/10 text-warning"
          aria-label="Flagged as suspicious"
        >
          <AlertTriangle className="size-3" aria-hidden="true" />
          Suspicious
        </span>
      )}
      {showLowConfidence && (
        <span className="text-[11px] text-warning shrink-0" aria-label="Low confidence score">
          Low confidence
        </span>
      )}
    </>
  )
}

interface ApprovalCardHeaderProps {
  approval: ApprovalResponse
  selected: boolean
  isPending: boolean
  countdown: number | null
  riskColor: ReturnType<typeof getRiskLevelColor>
  urgencyColor: ReturnType<typeof getUrgencyColor>
  onSelect: (id: string) => void
  onToggleSelect: (id: string) => void
}

function ApprovalCardHeader(props: ApprovalCardHeaderProps) {
  const { approval, selected, isPending, countdown, riskColor, urgencyColor } = props
  return (
    <div className="flex items-start gap-3">
      {isPending && (
        <input
          type="checkbox"
          checked={selected}
          onChange={() => props.onToggleSelect(approval.id)}
          className="mt-1 size-4 shrink-0 accent-accent"
          aria-label={`Select ${approval.title}`}
        />
      )}

      <span
        className={cn(
          'mt-1.5 size-2 shrink-0 rounded-full',
          DOT_COLOR_CLASSES[riskColor],
          approval.urgency_level === 'critical' && isPending && 'animate-pulse',
        )}
        aria-label={`Risk: ${getRiskLevelLabel(approval.risk_level)}`}
      />

      <div className="min-w-0 flex-1">
        <button
          type="button"
          onClick={() => props.onSelect(approval.id)}
          className="text-left text-sm font-medium text-foreground hover:text-accent transition-colors truncate block w-full"
        >
          {approval.title}
        </button>
        <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
          <span className="font-mono">{approval.action_type}</span>
          <span aria-hidden="true">--</span>
          <span>{approval.requested_by}</span>
        </div>
      </div>

      <ApprovalBadges
        isPending={isPending}
        countdown={countdown}
        urgencyColor={urgencyColor}
        isBlocked={approval.metadata['safety_classification'] === 'blocked'}
        isSuspicious={approval.metadata['safety_classification'] === 'suspicious'}
        showLowConfidence={isLowConfidence(approval)}
      />
    </div>
  )
}

function ApprovalCardActions({
  id,
  onApprove,
  onReject,
}: {
  id: string
  onApprove: (id: string) => void
  onReject: (id: string) => void
}) {
  return (
    <div className="mt-3 flex items-center gap-2">
      <Button
        size="sm"
        variant="outline"
        className="h-7 gap-1 border-success/30 text-success hover:bg-success/10"
        onClick={() => onApprove(id)}
      >
        <Check className="size-3.5" />
        Approve
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="h-7 gap-1 border-danger/30 text-danger hover:bg-danger/10"
        onClick={() => onReject(id)}
      >
        <X className="size-3.5" />
        Reject
      </Button>
    </div>
  )
}

function ApprovalCardImpl({
  approval,
  selected,
  onSelect,
  onApprove,
  onReject,
  onToggleSelect,
  className,
}: ApprovalCardProps) {
  const isPending = approval.status === 'pending'
  const flashStyle = useStatusFlash(approval.status)
  const countdown = useApprovalCountdown(approval.seconds_remaining, isPending)

  return (
    <div
      className={cn(
        'rounded-lg border bg-card p-card transition-all duration-200',
        selected ? 'border-bright ring-1 ring-accent/20' : 'border-border',
        isPending && 'hover:bg-card-hover hover:-translate-y-px hover:shadow-md',
        !isPending && 'opacity-70',
        className,
      )}
      style={flashStyle}
      role="article"
      aria-label={`Approval: ${approval.title}`}
    >
      <ApprovalCardHeader
        approval={approval}
        selected={selected}
        isPending={isPending}
        countdown={countdown}
        riskColor={getRiskLevelColor(approval.risk_level)}
        urgencyColor={getUrgencyColor(approval.urgency_level)}
        onSelect={onSelect}
        onToggleSelect={onToggleSelect}
      />

      {isPending && <ApprovalCardActions id={approval.id} onApprove={onApprove} onReject={onReject} />}
    </div>
  )
}

/**
 * Memoised so a parent re-render that doesn't change the approval ref
 * skips the per-card flash + countdown effects. Card count is unbounded
 * (paginated approvals queue); without memo every row re-renders on any
 * sibling state change.
 */
export const ApprovalCard = memo(ApprovalCardImpl)

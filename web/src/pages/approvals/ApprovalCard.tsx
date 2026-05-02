import { memo, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Check, Clock, ShieldOff, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { useFlash } from '@/hooks/useFlash'
import { DOT_COLOR_CLASSES, URGENCY_BADGE_CLASSES, formatUrgency, getRiskLevelColor, getRiskLevelLabel, getUrgencyColor } from '@/utils/approvals'
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

function ApprovalCardImpl({
  approval,
  selected,
  onSelect,
  onApprove,
  onReject,
  onToggleSelect,
  className,
}: ApprovalCardProps) {
  const riskColor = getRiskLevelColor(approval.risk_level)
  const urgencyColor = getUrgencyColor(approval.urgency_level)
  const isPending = approval.status === 'pending'
  const safetyClassification = approval.metadata.safety_classification
  const isSuspicious = safetyClassification === 'suspicious'
  const isBlocked = safetyClassification === 'blocked'
  const confidenceRaw = approval.metadata.confidence_score
  const confidenceScore = confidenceRaw != null ? parseFloat(confidenceRaw) : NaN
  const showLowConfidence = approval.metadata.low_confidence === 'true'
    || (!Number.isNaN(confidenceScore) && confidenceScore < 0.5)

  // Flash on status change
  const { flashStyle, triggerFlash } = useFlash()
  const prevStatusRef = useRef(approval.status)
  useEffect(() => {
    if (approval.status !== prevStatusRef.current) {
      triggerFlash()
      prevStatusRef.current = approval.status
    }
  }, [approval.status, triggerFlash])

  // Local countdown -- tracks seconds_remaining with a 1s tick.
  // Reset + interval-restart are intentionally collapsed into ONE
  // effect keyed on the prop so a refresh from the WS layer
  // (seconds_remaining bumped from e.g. 5 to 60) restarts the
  // interval on a clean 1-second cadence instead of inheriting the
  // previous interval's mid-tick offset (which can flicker by a
  // sub-second when the prop changes mid-tick). setCountdown is
  // deferred to a microtask so the effect body stays free of
  // synchronous setState per the ESLint set-state-in-effect rule;
  // setInterval starts immediately so the visible tick cadence
  // matches the prop refresh. The ``cancelled`` flag is checked in
  // BOTH the microtask AND the timer callback so a tick that fires
  // between the prop refresh and the cleanup running cannot
  // decrement a freshly-set countdown by one.
  const [countdown, setCountdown] = useState(approval.seconds_remaining)
  useEffect(() => {
    let cancelled = false
    void Promise.resolve().then(() => {
      if (cancelled) return
      setCountdown(approval.seconds_remaining)
    })
    if (
      !isPending
      || approval.seconds_remaining === null
      || approval.seconds_remaining <= 0
    ) {
      return () => {
        cancelled = true
      }
    }
    const timer = setInterval(() => {
      if (cancelled) return
      setCountdown((prev) => {
        if (prev === null || prev <= 1) return 0
        return prev - 1
      })
    }, 1000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [approval.seconds_remaining, isPending])

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
      {/* Header row */}
      <div className="flex items-start gap-3">
        {/* Checkbox (only for pending) */}
        {isPending && (
          <input
            type="checkbox"
            checked={selected}
            onChange={() => onToggleSelect(approval.id)}
            className="mt-1 size-4 shrink-0 accent-accent"
            aria-label={`Select ${approval.title}`}
          />
        )}

        {/* Risk dot */}
        <span
          className={cn(
            'mt-1.5 size-2 shrink-0 rounded-full',
            DOT_COLOR_CLASSES[riskColor],
            approval.urgency_level === 'critical' && isPending && 'animate-pulse',
          )}
          aria-label={`Risk: ${getRiskLevelLabel(approval.risk_level)}`}
        />

        {/* Content */}
        <div className="min-w-0 flex-1">
          <button
            type="button"
            onClick={() => onSelect(approval.id)}
            className="text-left text-sm font-medium text-foreground hover:text-accent transition-colors truncate block w-full"
          >
            {approval.title}
          </button>
          <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-secondary">
            <span className="font-mono">{approval.action_type}</span>
            <span aria-hidden="true">--</span>
            <span>{approval.requested_by}</span>
          </div>
        </div>

        {/* Urgency badge */}
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

        {/* Safety classification badge */}
        {isBlocked && (
          <span
            className={cn(
              'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium shrink-0',
              'border-danger/30 bg-danger/10 text-danger',
            )}
            aria-label="Blocked by safety classifier"
          >
            <ShieldOff className="size-3" aria-hidden="true" />
            Blocked
          </span>
        )}
        {isSuspicious && (
          <span
            className={cn(
              'inline-flex items-center gap-1 rounded border px-1.5 py-0.5 text-[11px] font-medium shrink-0',
              'border-warning/30 bg-warning/10 text-warning',
            )}
            aria-label="Flagged as suspicious"
          >
            <AlertTriangle className="size-3" aria-hidden="true" />
            Suspicious
          </span>
        )}

        {/* Low confidence indicator */}
        {showLowConfidence && (
          <span className="text-[11px] text-warning shrink-0" aria-label="Low confidence score">
            Low confidence
          </span>
        )}
      </div>

      {/* Action buttons (pending only) */}
      {isPending && (
        <div className="mt-3 flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 border-success/30 text-success hover:bg-success/10"
            onClick={() => onApprove(approval.id)}
          >
            <Check className="size-3.5" />
            Approve
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="h-7 gap-1 border-danger/30 text-danger hover:bg-danger/10"
            onClick={() => onReject(approval.id)}
          >
            <X className="size-3.5" />
            Reject
          </Button>
        </div>
      )}
    </div>
  )
}

/**
 * Memoised so a parent re-render that doesn't change the approval ref
 * skips the per-card flash + countdown effects below. Card count is
 * unbounded (paginated approvals queue); without memo every row
 * re-renders on any sibling state change.
 */
export const ApprovalCard = memo(ApprovalCardImpl)

import { memo, useEffect, useRef, useState } from 'react'
import { AlertTriangle, Clock, ShieldOff } from 'lucide-react'
import { cn } from '@/lib/utils'
import { RunOutcomeBadge } from '@/components/ui/run-outcome-badge'
import { StatusPill } from '@/components/ui/status-pill'
import { ApprovalDecisionButtons } from './ApprovalDecisionButtons'
import { useFlash } from '@/hooks/useFlash'
import {
  DOT_COLOR_CLASSES,
  URGENCY_BADGE_CLASSES,
  formatUrgency,
  getApprovalStepLabel,
  getRiskLevelColor,
  getRiskLevelLabel,
  getUrgencyColor,
  isFailedApproval,
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
 * Local 1s countdown over `seconds_remaining`. A WS refresh re-syncs the
 * countdown to the new server value via the sanctioned adjust-state-on-
 * prop-change pattern (a `useState` previous-value tracker compared during
 * render), keeping both setStates inside React's render bookkeeping so a
 * discarded concurrent render cannot leave the tracker ahead of the value.
 * A separate effect owns the 1-Hz interval, and a sibling effect stops it
 * once the countdown reaches zero so an expired card in the paginated queue
 * does not keep a dormant interval running.
 */
function useApprovalCountdown(secondsRemaining: number | null, isPending: boolean): number | null {
  const [countdown, setCountdown] = useState(secondsRemaining)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const [prevSeconds, setPrevSeconds] = useState(secondsRemaining)

  if (secondsRemaining !== prevSeconds) {
    setPrevSeconds(secondsRemaining)
    setCountdown(secondsRemaining)
  }

  useEffect(() => {
    if (!isPending || secondsRemaining === null || secondsRemaining <= 0) {
      return
    }
    const timer = setInterval(() => {
      setCountdown((prev) => (prev === null || prev <= 1 ? 0 : prev - 1))
    }, 1000)
    timerRef.current = timer
    return () => {
      clearInterval(timer)
      timerRef.current = null
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
        <StatusPill
          toneClassName={URGENCY_BADGE_CLASSES[urgencyColor]}
          icon={Clock}
          className="font-mono"
          ariaLabel={`Expires in ${formatUrgency(countdown)}`}
        >
          <span aria-hidden="true">{formatUrgency(countdown)}</span>
        </StatusPill>
      )}
      {isPending && countdown === null && (
        <span className="text-micro text-muted-foreground shrink-0">No expiry</span>
      )}
      {isBlocked && (
        <StatusPill tone="danger" icon={ShieldOff} ariaLabel="Blocked by safety classifier">
          Blocked
        </StatusPill>
      )}
      {isSuspicious && (
        <StatusPill tone="warning" icon={AlertTriangle} ariaLabel="Flagged as suspicious">
          Suspicious
        </StatusPill>
      )}
      {showLowConfidence && (
        <span className="text-micro text-warning shrink-0" aria-label="Low confidence score">
          Low confidence
        </span>
      )}
    </>
  )
}

/** Step label + resolved project / agent names (no raw UUIDs). */
function ApprovalCardMeta({ approval }: { approval: ApprovalResponse }) {
  return (
    <div className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-text-secondary">
      <span>{getApprovalStepLabel(approval)}</span>
      {approval.project && (
        <>
          <span aria-hidden="true">·</span>
          <span>{approval.project.name}</span>
        </>
      )}
      <span aria-hidden="true">·</span>
      <span>{approval.agent?.name ?? approval.requested_by}</span>
    </div>
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
          {approval.task?.title ?? approval.title}
        </button>
        <ApprovalCardMeta approval={approval} />
      </div>

      <div className="flex shrink-0 flex-wrap items-center justify-end gap-2">
        {approval.run && <RunOutcomeBadge outcome={approval.run.outcome} />}
        <ApprovalBadges
          isPending={isPending}
          countdown={countdown}
          urgencyColor={urgencyColor}
          isBlocked={approval.metadata['safety_classification'] === 'blocked'}
          isSuspicious={approval.metadata['safety_classification'] === 'suspicious'}
          showLowConfidence={isLowConfidence(approval)}
        />
      </div>
    </div>
  )
}

function ApprovalCardActions({
  id,
  isFailed,
  onApprove,
  onReject,
}: {
  id: string
  isFailed: boolean
  onApprove: (id: string) => void
  onReject: (id: string) => void
}) {
  return (
    <ApprovalDecisionButtons
      isFailed={isFailed}
      onApprove={() => onApprove(id)}
      onReject={() => onReject(id)}
      className="mt-3"
    />
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
  const isFailed = isFailedApproval(approval)
  const flashStyle = useStatusFlash(approval.status)
  const countdown = useApprovalCountdown(approval.seconds_remaining, isPending)

  return (
    <div
      className={cn(
        'rounded-lg border p-card transition-all duration-200',
        // A failed run is visually unmistakable: danger-tinted surface and
        // border so it is never read as a routine completion.
        isFailed ? 'border-danger/40 bg-danger/5' : 'bg-card',
        selected ? 'border-bright ring-1 ring-accent/20' : !isFailed && 'border-border',
        isPending && 'hover:bg-card-hover hover:-translate-y-px hover:shadow-[var(--so-shadow-card-hover)]',
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

      {isPending && (
        <ApprovalCardActions
          id={approval.id}
          isFailed={isFailed}
          onApprove={onApprove}
          onReject={onReject}
        />
      )}
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

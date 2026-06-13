import { cn } from '@/lib/utils'
import { getApprovalStatusColor, getApprovalStatusLabel } from '@/utils/approvals'
import { formatDateTime } from '@/utils/format'
import type { ApprovalResponse } from '@/api/types/approvals'

export interface ApprovalTimelineProps {
  approval: ApprovalResponse
  className?: string
}

interface StepDef {
  label: string
  state: 'complete' | 'active' | 'future'
  timestamp?: string | null | undefined
  outcomeLabel?: string | undefined
}

function getSteps(approval: ApprovalResponse): StepDef[] {
  const decided = approval.status === 'approved' || approval.status === 'rejected' || approval.status === 'expired'

  return [
    {
      label: 'Submitted',
      state: 'complete',
      timestamp: approval.created_at,
    },
    {
      label: 'Under Review',
      state: decided ? 'complete' : 'active',
    },
    {
      label: 'Decided',
      state: decided ? 'complete' : 'future',
      timestamp: approval.decided_at,
      outcomeLabel: decided ? getApprovalStatusLabel(approval.status) : undefined,
    },
  ]
}

const DOT_CLASSES = {
  complete: 'bg-success border-success',
  active: 'bg-accent border-accent animate-pulse',
  future: 'bg-border border-border',
} as const

const LINE_CLASSES = {
  complete: 'bg-success',
  active: 'bg-accent',
  future: 'bg-border border-dashed border-t',
} as const

const OUTCOME_CLASSES: Record<string, string> = {
  success: 'text-success',
  danger: 'text-danger',
  'text-secondary': 'text-text-secondary',
}

export function ApprovalTimeline({ approval, className }: ApprovalTimelineProps) {
  const steps = getSteps(approval)

  return (
    <div className={cn('flex items-start', className)} role="list" aria-label="Approval timeline">
      {steps.map((step, idx) => (
        <div key={step.label} className="flex flex-1 items-start" role="listitem">
          <div className="flex flex-col items-center">
            {/* Dot */}
            <span
              className={cn(
                'size-3 shrink-0 rounded-full border-2',
                DOT_CLASSES[step.state],
              )}
              aria-hidden="true"
            />
            {/* Label */}
            <span className="mt-1.5 text-[10px] font-medium text-text-secondary">{step.label}</span>
            {/* Timestamp */}
            {step.timestamp && (
              <span className="mt-0.5 font-mono text-[9px] text-muted-foreground">
                {formatDateTime(step.timestamp)}
              </span>
            )}
            {/* Outcome badge */}
            {step.outcomeLabel && (
              <span
                className={cn(
                  'mt-0.5 text-[10px] font-semibold',
                  OUTCOME_CLASSES[getApprovalStatusColor(approval.status)] ?? 'text-text-secondary',
                )}
              >
                {step.outcomeLabel}
              </span>
            )}
          </div>
          {/* Connecting line */}
          {idx < steps.length - 1 && (
            <div
              className={cn(
                'mt-1.5 h-0.5 flex-1 mx-1',
                LINE_CLASSES[step.state],
              )}
              aria-hidden="true"
            />
          )}
        </div>
      ))}
    </div>
  )
}

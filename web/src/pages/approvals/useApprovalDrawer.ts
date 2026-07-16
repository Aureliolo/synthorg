import { useCallback, useRef, useState } from 'react'
import { REJECTION_REASON_REQUIRED } from './errors'
import { getRiskLevelColor } from '@/utils/approvals'
import type { SemanticColor } from '@/utils/agent-status'
import { useToastStore } from '@/stores/toast'
import type { ApprovalResponse, ApproveRequest, RejectRequest } from '@/api/types/approvals'

/** Run `onChange` during render whenever `value`'s identity changes. */
function useResetOnChange(value: unknown, onChange: () => void): void {
  const prevRef = useRef(value)
  if (value !== prevRef.current) {
    prevRef.current = value
    onChange()
  }
}

export interface ApprovalDecision {
  approveOpen: boolean
  setApproveOpen: (open: boolean) => void
  rejectOpen: boolean
  setRejectOpen: (open: boolean) => void
  comment: string
  setComment: (value: string) => void
  reason: string
  setReason: (value: string) => void
  reasonError: string | null
  setReasonError: (value: string | null) => void
  submitting: boolean
  isPending: boolean
  riskColor: SemanticColor | 'accent-dim'
  confidenceLabel: string | null
  handleApprove: () => Promise<boolean | undefined>
  handleReject: () => Promise<boolean | undefined>
}

function confidenceLabelFor(approval: ApprovalResponse | null): string | null {
  const raw = approval?.metadata['confidence_score']
  const score = raw != null ? parseFloat(raw) : NaN
  return !Number.isNaN(score) ? `${(score * 100).toFixed(0)}%` : null
}

interface ApproveDeps {
  approval: ApprovalResponse | null
  comment: string
  onApprove: (id: string, data?: ApproveRequest) => Promise<boolean>
  setSubmitting: (value: boolean) => void
  setComment: (value: string) => void
}

async function approveDecision(deps: ApproveDeps): Promise<boolean | undefined> {
  const { approval } = deps
  if (!approval || approval.status !== 'pending') return
  deps.setSubmitting(true)
  try {
    const trimmed = deps.comment.trim()
    const ok = await deps.onApprove(approval.id, trimmed ? { comment: trimmed } : undefined)
    if (ok) {
      deps.setComment('')
      return true
    }
    return false
  } finally {
    deps.setSubmitting(false)
  }
}

interface RejectDeps {
  approval: ApprovalResponse | null
  reason: string
  onReject: (id: string, data: RejectRequest) => Promise<boolean>
  setSubmitting: (value: boolean) => void
  setReason: (value: string) => void
  setReasonError: (value: string | null) => void
}

async function rejectDecision(deps: RejectDeps): Promise<boolean | undefined> {
  const { approval } = deps
  if (!approval || approval.status !== 'pending') return
  if (!deps.reason.trim()) {
    deps.setReasonError(REJECTION_REASON_REQUIRED)
    useToastStore.getState().add({
      variant: 'error',
      title: 'Rejection reason required',
      description: REJECTION_REASON_REQUIRED,
    })
    return false
  }
  deps.setReasonError(null)
  deps.setSubmitting(true)
  try {
    const ok = await deps.onReject(approval.id, { reason: deps.reason.trim() })
    if (ok) {
      deps.setReason('')
      return true
    }
    return false
  } finally {
    deps.setSubmitting(false)
  }
}

export function useApprovalDecision(
  approval: ApprovalResponse | null,
  onApprove: (id: string, data?: ApproveRequest) => Promise<boolean>,
  onReject: (id: string, data: RejectRequest) => Promise<boolean>,
  // When true, a fresh non-null target opens the reject dialog in the same
  // render pass the target changes. The card-reject flow sets this so its
  // "click Reject -> dialog" step needs no post-commit effect racing this
  // reset; the drawer leaves it false (a target switch closes the dialog).
  openRejectOnTargetChange = false,
): ApprovalDecision {
  const [approveOpen, setApproveOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [comment, setComment] = useState('')
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const isPending = approval?.status === 'pending'

  // Reset dialog/input state when the displayed approval changes.
  useResetOnChange(approval?.id, () => {
    setApproveOpen(false)
    setRejectOpen(openRejectOnTargetChange && approval != null)
    setComment('')
    setReason('')
    setReasonError(null)
    setSubmitting(false)
  })

  // Close confirm dialogs once the approval is no longer pending (e.g.
  // decided via WebSocket while the drawer is open).
  useResetOnChange(isPending, () => {
    if (!isPending) {
      setApproveOpen(false)
      setRejectOpen(false)
      setReasonError(null)
    }
  })

  const handleApprove = useCallback(
    () => approveDecision({ approval, comment, onApprove, setSubmitting, setComment }),
    [approval, comment, onApprove],
  )

  const handleReject = useCallback(
    () => rejectDecision({ approval, reason, onReject, setSubmitting, setReason, setReasonError }),
    [approval, reason, onReject],
  )

  return {
    approveOpen,
    setApproveOpen,
    rejectOpen,
    setRejectOpen,
    comment,
    setComment,
    reason,
    setReason,
    reasonError,
    setReasonError,
    submitting,
    isPending,
    riskColor: approval ? getRiskLevelColor(approval.risk_level) : 'accent',
    confidenceLabel: confidenceLabelFor(approval),
    handleApprove,
    handleReject,
  }
}

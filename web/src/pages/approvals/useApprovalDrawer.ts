import { useCallback, useRef, useState } from 'react'
import { REJECTION_REASON_REQUIRED } from './errors'
import { getRiskLevelColor } from '@/utils/approvals'
import type { SemanticColor } from '@/utils/agent-status'
import { useToastStore } from '@/stores/toast'
import type {
  ApprovalResponse,
  ApproveRequest,
  RejectRequest,
} from '@/api/types/approvals'
import type { PlanOption } from '@/api/types/plans'

const CHOICE_REQUIRED = 'Choose an option before approving.'

/** The execution-time decision options an approval offers, or empty. */
function decisionOptionsOf(
  approval: ApprovalResponse | null,
): readonly PlanOption[] {
  return approval?.evidence_package?.options ?? []
}

/**
 * The option to show selected. A decided approval carries the operator's actual
 * pick on ``evidence_package.chosen_option_id``, so surface that; a pending fork
 * has none yet, so the recommended option (or the first) is shown selected as the
 * default the operator can change.
 */
/** The recommended option (or the first) as the default selection. */
function fallbackOptionId(options: readonly PlanOption[]): string | null {
  return options.find((o) => o.recommended)?.id ?? options[0]?.id ?? null
}

/** Reset key that changes when an approval's persisted decision or status does. */
function decisionSyncKey(approval: ApprovalResponse | null): string {
  return `${approval?.evidence_package?.chosen_option_id ?? ''}|${approval?.status ?? ''}`
}

function defaultChosenOptionId(approval: ApprovalResponse | null): string | null {
  const options = decisionOptionsOf(approval)
  const persisted = approval?.evidence_package?.chosen_option_id
  if (persisted != null && options.some((o) => o.id === persisted)) {
    return persisted
  }
  return fallbackOptionId(options)
}

/**
 * Build the approve payload. A decision fork carries the operator's
 * ``chosen_option_id`` (the backend resolves it to the option writeup the
 * parked agent resumes with); a plain approval carries only an optional
 * comment.
 */
function buildApproveRequest(
  approval: ApprovalResponse,
  comment: string,
  chosenOptionId: string | null,
): ApproveRequest | undefined {
  const trimmed = comment.trim()
  if (decisionOptionsOf(approval).length > 0) {
    return {
      chosen_option_id: chosenOptionId,
      ...(trimmed && { comment: trimmed }),
    }
  }
  return trimmed ? { comment: trimmed } : undefined
}

/** Run `onChange` during render whenever `value`'s identity changes. */
export function useResetOnChange(value: unknown, onChange: () => void): void {
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
  /** The chosen decision-option id, or ``null`` for a plain approval. */
  chosenOptionId: string | null
  setChosenOptionId: (value: string) => void
  /** Decision-fork options this approval offers (empty for a plain approval). */
  options: readonly PlanOption[]
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
  chosenOptionId: string | null
  onApprove: (id: string, data?: ApproveRequest) => Promise<boolean>
  setSubmitting: (value: boolean) => void
  setComment: (value: string) => void
}

async function approveDecision(deps: ApproveDeps): Promise<boolean | undefined> {
  const { approval } = deps
  if (!approval || approval.status !== 'pending') return
  if (decisionOptionsOf(approval).length > 0 && !deps.chosenOptionId) {
    useToastStore.getState().add({
      variant: 'error',
      title: 'Choice required',
      description: CHOICE_REQUIRED,
    })
    return false
  }
  deps.setSubmitting(true)
  try {
    const data = buildApproveRequest(approval, deps.comment, deps.chosenOptionId)
    const ok = await deps.onApprove(approval.id, data)
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
  const [chosenOptionId, setChosenOptionId] = useState<string | null>(() =>
    defaultChosenOptionId(approval),
  )
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const isPending = approval?.status === 'pending'

  // Reset dialog/input state when the displayed approval changes.
  useResetOnChange(approval?.id, () => {
    setApproveOpen(false)
    setRejectOpen(openRejectOnTargetChange && approval != null)
    setComment('')
    setChosenOptionId(defaultChosenOptionId(approval))
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

  // Re-derive the shown choice when the persisted decision or status changes on
  // the SAME approval (a WebSocket update decides the displayed approval): the
  // id-keyed reset above misses it, so without this the drawer keeps showing
  // the stale default instead of the operator's recorded pick.
  useResetOnChange(decisionSyncKey(approval), () => {
    setChosenOptionId(defaultChosenOptionId(approval))
  })

  const handleApprove = useCallback(
    () =>
      approveDecision({
        approval,
        comment,
        chosenOptionId,
        onApprove,
        setSubmitting,
        setComment,
      }),
    [approval, comment, chosenOptionId, onApprove],
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
    chosenOptionId,
    setChosenOptionId,
    options: decisionOptionsOf(approval),
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

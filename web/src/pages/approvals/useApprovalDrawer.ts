import { useCallback, useEffect, useRef, useState } from 'react'
import type { RefObject } from 'react'
import { REJECTION_REASON_REQUIRED } from './errors'
import { getRiskLevelColor } from '@/utils/approvals'
import { useToastStore } from '@/stores/toast'
import type { ApprovalResponse, ApproveRequest, RejectRequest } from '@/api/types/approvals'

const FOCUSABLE_SELECTOR =
  'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'

/** Close on Escape, unless a nested confirmation dialog is open. */
export function useEscapeToClose(active: boolean, onClose: () => void, blocked: boolean): void {
  useEffect(() => {
    if (!active) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !blocked) onClose()
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [active, onClose, blocked])
}

/** Remember the opener element and restore focus to it on close. */
export function useRestoreFocusOnClose(open: boolean): void {
  const openerRef = useRef<Element | null>(null)
  useEffect(() => {
    if (open) openerRef.current = document.activeElement
    return () => {
      if (openerRef.current instanceof HTMLElement) openerRef.current.focus()
      openerRef.current = null
    }
  }, [open])
}

/**
 * Keep Tab cycling within the panel. `reengage` re-runs the effect when
 * async content arrives so focus moves onto the first real control.
 */
export function useFocusTrap(
  panelRef: RefObject<HTMLElement | null>,
  active: boolean,
  reengage: unknown,
): void {
  useEffect(() => {
    if (!active) return
    void reengage
    const panel = panelRef.current
    if (!panel) return
    const focusable = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
    if (focusable.length > 0) {
      focusable[0]!.focus()
    } else {
      panel.setAttribute('tabindex', '-1')
      panel.focus()
    }

    const handleTab = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const nodes = panel.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)
      if (nodes.length === 0) return
      const first = nodes[0]!
      const last = nodes[nodes.length - 1]!
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault()
        last.focus()
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', handleTab)
    return () => document.removeEventListener('keydown', handleTab)
  }, [active, reengage, panelRef])
}

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
  riskColor: string
  confidenceLabel: string | null
  handleApprove: () => Promise<boolean | void>
  handleReject: () => Promise<boolean | void>
}

function confidenceLabelFor(approval: ApprovalResponse | null): string | null {
  const raw = approval?.metadata.confidence_score
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

async function approveDecision(deps: ApproveDeps): Promise<boolean | void> {
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

async function rejectDecision(deps: RejectDeps): Promise<boolean | void> {
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
    setRejectOpen(false)
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

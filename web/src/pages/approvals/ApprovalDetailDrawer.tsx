import { useCallback, useEffect, useRef, useState } from 'react'
import { motion } from 'motion/react'
import { AlertTriangle, Calendar, Check, Loader2, Shield, Tag, User, X, type LucideIcon } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { ErrorBanner } from '@/components/ui/error-banner'
import { InputField } from '@/components/ui/input-field'
import { springDefault, overlayBackdrop, tweenExitFast } from '@/lib/motion'
import { ApprovalTimeline } from './ApprovalTimeline'
import {
  getApprovalStatusLabel,
  getRiskLevelColor,
  getRiskLevelLabel,
  formatUrgency,
} from '@/utils/approvals'
import { formatDate } from '@/utils/format'
import { useToastStore } from '@/stores/toast'
import type { ApprovalResponse, ApproveRequest, RejectRequest } from '@/api/types/approvals'

export interface ApprovalDetailDrawerProps {
  approval: ApprovalResponse | null
  open: boolean
  onClose: () => void
  /**
   * Resolve to ``true`` on success, ``false`` on failure. The drawer
   * uses the boolean to decide whether to close the confirmation
   * dialog and reset its inputs. Failure UX (toast / banner) is owned
   * by the caller's underlying store mutation -- the drawer does NOT
   * try/catch.
   */
  onApprove: (id: string, data?: ApproveRequest) => Promise<boolean>
  onReject: (id: string, data: RejectRequest) => Promise<boolean>
  loading?: boolean
  error?: string | null
}

const PANEL_VARIANTS = {
  initial: { x: '100%', opacity: 0 },
  animate: { x: 0, opacity: 1, transition: springDefault },
  exit: { x: '100%', opacity: 0, transition: tweenExitFast },
}

const RISK_DOT_CLASSES: Record<string, string> = {
  danger: 'bg-danger',
  warning: 'bg-warning',
  accent: 'bg-accent',
  'accent-dim': 'bg-accent-dim',
}

const RISK_BADGE_CLASSES: Record<string, string> = {
  danger: 'border-danger/30 bg-danger/10 text-danger',
  warning: 'border-warning/30 bg-warning/10 text-warning',
  accent: 'border-accent/30 bg-accent/10 text-accent',
  'accent-dim': 'border-accent-dim/30 bg-accent-dim/10 text-accent-dim',
}

export function ApprovalDetailDrawer({
  approval,
  open,
  onClose,
  onApprove,
  onReject,
  loading,
  error: detailError,
}: ApprovalDetailDrawerProps) {
  const [approveOpen, setApproveOpen] = useState(false)
  const [rejectOpen, setRejectOpen] = useState(false)
  const [comment, setComment] = useState('')
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const isPending = approval?.status === 'pending'
  const riskColor = approval ? getRiskLevelColor(approval.risk_level) : 'accent'
  const confidenceRaw = approval?.metadata.confidence_score
  const confidenceScore = confidenceRaw != null ? parseFloat(confidenceRaw) : NaN
  const confidenceLabel = !Number.isNaN(confidenceScore) ? `${(confidenceScore * 100).toFixed(0)}%` : null
  const panelRef = useRef<HTMLElement>(null)
  const openerRef = useRef<Element | null>(null)

  // Reset dialog/input state when the displayed approval changes
  const prevApprovalIdRef = useRef(approval?.id)
  if (approval?.id !== prevApprovalIdRef.current) {
    prevApprovalIdRef.current = approval?.id
    setApproveOpen(false)
    setRejectOpen(false)
    setComment('')
    setReason('')
    setReasonError(null)
    setSubmitting(false)
  }

  // Close confirm dialogs if approval is no longer pending (e.g., decided via WebSocket)
  const prevIsPendingRef = useRef(isPending)
  if (isPending !== prevIsPendingRef.current) {
    if (!isPending) {
      setApproveOpen(false)
      setRejectOpen(false)
      setReasonError(null)
    }
    prevIsPendingRef.current = isPending
  }

  // Close on Escape (skip when any confirmation dialog is open)
  useEffect(() => {
    if (!open) return
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (approveOpen || rejectOpen) return
        onClose()
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [open, onClose, approveOpen, rejectOpen])

  // Save opener reference and restore focus on close
  useEffect(() => {
    if (open) openerRef.current = document.activeElement
    return () => {
      if (openerRef.current instanceof HTMLElement) openerRef.current.focus()
      openerRef.current = null
    }
  }, [open])

  // Focus trap -- keep Tab cycling within the panel.
  // Re-runs when loading/approval changes so focus engages after content arrives.
  useEffect(() => {
    if (!open) return
    const panel = panelRef.current
    if (!panel) return
    const focusable = panel.querySelectorAll<HTMLElement>(
      'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
    )
    if (focusable.length > 0) {
      focusable[0]!.focus()
    } else {
      // Fallback: make the panel itself focusable during loading state
      panel.setAttribute('tabindex', '-1')
      panel.focus()
    }

    const handleTab = (e: KeyboardEvent) => {
      if (e.key !== 'Tab') return
      const nodes = panel.querySelectorAll<HTMLElement>(
        'button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      )
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
  }, [open, loading, approval])

  const handleApprove = useCallback(async (): Promise<boolean | void> => {
    if (!approval || approval.status !== 'pending') return
    setSubmitting(true)
    try {
      const ok = await onApprove(
        approval.id,
        comment.trim() ? { comment: comment.trim() } : undefined,
      )
      if (ok) {
        // Inputs reset before ConfirmDialog closes itself.
        setComment('')
        return true
      }
      // Returning false keeps the ConfirmDialog open so the user can retry.
      return false
    } finally {
      // Always clear the in-flight flag so the button doesn't stay
      // stuck spinning if the sentinel contract ever surfaces a throw.
      setSubmitting(false)
    }
  }, [approval, comment, onApprove])

  const handleReject = useCallback(async (): Promise<boolean | void> => {
    if (!approval || approval.status !== 'pending') return
    if (!reason.trim()) {
      // Inline field error so the user sees a red border + helper
      // text on the InputField itself, not just a toast that flies
      // away. The toast remains as a secondary live-region signal.
      setReasonError(
        'Rejection requires a reason for the approval record. Provide a brief explanation.',
      )
      useToastStore.getState().add({
        variant: 'error',
        title: 'Rejection reason required',
        description:
          'Rejection requires a reason for the approval record. Provide a brief explanation.',
      })
      return false
    }
    setReasonError(null)
    setSubmitting(true)
    try {
      const ok = await onReject(approval.id, { reason: reason.trim() })
      if (ok) {
        setReason('')
        return true
      }
      return false
    } finally {
      setSubmitting(false)
    }
  }, [approval, reason, onReject])

  if (!open) return null

  const showLoadingState = loading || !approval

  return (
    <>
      {/* Backdrop */}
      <motion.div
        className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
        variants={overlayBackdrop}
        initial="initial"
        animate="animate"
        exit="exit"
        onClick={onClose}
      />

      {/* Panel */}
      <motion.aside
        ref={panelRef}
        className="fixed top-0 right-0 z-50 flex h-full w-full max-w-lg flex-col border-l border-border bg-base shadow-[var(--so-shadow-card-hover)]"
        variants={PANEL_VARIANTS}
        initial="initial"
        animate="animate"
        exit="exit"
        role="dialog"
        aria-modal="true"
        aria-label={approval ? `Approval detail: ${approval.title}` : 'Approval detail'}
      >
        {showLoadingState && !detailError && (
          <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading approval">
            <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
          </div>
        )}

        {detailError && (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
            <AlertTriangle className="size-8 text-danger" />
            <p className="text-sm text-danger">{detailError}</p>
            <Button variant="ghost" size="sm" onClick={onClose}>Close</Button>
          </div>
        )}

        {!detailError && !showLoadingState && approval && (
          <>
            {/* Header */}
            <div className="flex items-center justify-between border-b border-border px-6 py-4">
              <div className="flex items-center gap-2">
                <span
                  className={cn('size-2 rounded-full', RISK_DOT_CLASSES[riskColor])}
                  aria-hidden="true"
                />
                <span
                  className={cn(
                    'inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none',
                    RISK_BADGE_CLASSES[riskColor],
                  )}
                >
                  {getRiskLevelLabel(approval.risk_level)}
                </span>
                <span className="text-xs text-secondary">
                  {getApprovalStatusLabel(approval.status)}
                </span>
              </div>
              <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel">
                <X className="size-4" />
              </Button>
            </div>

            {/* Content */}
            <div className="flex-1 overflow-y-auto px-6 py-4 space-y-section-gap">
              {/* Title */}
              <h2 className="text-lg font-semibold text-foreground">{approval.title}</h2>

              {approval.metadata.safety_classification === 'blocked' && (
                <ErrorBanner
                  variant="inline"
                  severity="error"
                  title="Safety classifier blocked this action"
                  description="Review the details carefully before overriding."
                />
              )}
              {approval.metadata.safety_classification === 'suspicious' && (
                <ErrorBanner
                  variant="inline"
                  severity="warning"
                  title="Safety classifier flagged this action as suspicious"
                  description="Inspect the action payload before approving."
                />
              )}

              {/* Description (stripped version shown when PII was redacted) */}
              {approval.description && (
                <DescriptionSection approval={approval} />
              )}

              {/* Timeline */}
              <div>
                <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Timeline
                </span>
                <ApprovalTimeline approval={approval} className="mt-2" />
              </div>

              {/* Metadata grid */}
              <div className="grid grid-cols-1 gap-grid-gap rounded-lg border border-border p-card md:grid-cols-2">
                <MetaField icon={Tag} label="Action Type" value={approval.action_type} />
                <MetaField icon={Shield} label="Risk Level" value={getRiskLevelLabel(approval.risk_level)} />
                <MetaField icon={User} label="Requested By" value={approval.requested_by} />
                <MetaField icon={Calendar} label="Created" value={formatDate(approval.created_at)} />
                {approval.expires_at && (
                  <MetaField icon={Calendar} label="Expires" value={formatUrgency(approval.seconds_remaining)} />
                )}
                {approval.decided_by && (
                  <MetaField icon={User} label="Decided By" value={approval.decided_by} />
                )}
                {approval.decided_at && (
                  <MetaField icon={Calendar} label="Decided At" value={formatDate(approval.decided_at)} />
                )}
                {confidenceLabel && (
                  <MetaField icon={Shield} label="Confidence" value={confidenceLabel} />
                )}
                {approval.metadata.safety_classification && (
                  <MetaField
                    icon={Shield}
                    label="Safety"
                    value={approval.metadata.safety_classification}
                  />
                )}
              </div>

              {/* Decision reason */}
              {approval.decision_reason && (
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Reason
                  </span>
                  <p className="mt-1 rounded border border-border bg-surface p-2 text-sm text-secondary">
                    {approval.decision_reason}
                  </p>
                </div>
              )}

              {/* Task link */}
              {approval.task_id && (
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Linked Task
                  </span>
                  <p className="mt-1 font-mono text-xs text-secondary">{approval.task_id}</p>
                </div>
              )}

              {/* Metadata */}
              {Object.keys(approval.metadata).length > 0 && (
                <div>
                  <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                    Metadata
                  </span>
                  <dl className="mt-1 space-y-1">
                    {Object.entries(approval.metadata).map(([key, value]) => (
                      <div key={key} className="flex items-center gap-2 text-xs">
                        <dt className="font-mono text-muted-foreground">{key}:</dt>
                        <dd className="text-secondary">
                          {typeof value === 'string'
                            ? value
                            : typeof value === 'object' && value !== null
                              ? JSON.stringify(value)
                              : String(value ?? '')}
                        </dd>
                      </div>
                    ))}
                  </dl>
                </div>
              )}
            </div>

            {/* Footer actions */}
            {isPending && (
              <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1 border-success/30 text-success hover:bg-success/10"
                  onClick={() => setApproveOpen(true)}
                >
                  <Check className="size-3.5" />
                  Approve
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
                  onClick={() => setRejectOpen(true)}
                >
                  <X className="size-3.5" />
                  Reject
                </Button>
              </div>
            )}
          </>
        )}
      </motion.aside>

      {/* Approve dialog */}
      <ConfirmDialog
        open={approveOpen}
        onOpenChange={(o) => { setApproveOpen(o); if (!o) setComment('') }}
        title="Approve Action"
        description="Are you sure you want to approve this action?"
        confirmLabel="Approve"
        onConfirm={handleApprove}
        loading={submitting}
      >
        <InputField
          multiline
          label="Optional comment"
          value={comment}
          onValueChange={setComment}
          placeholder="Add context for the requester..."
          rows={3}
          maxLength={2000}
          className="mt-2"
        />
      </ConfirmDialog>

      {/* Reject dialog */}
      <ConfirmDialog
        open={rejectOpen}
        onOpenChange={(o) => { setRejectOpen(o); if (!o) { setReason(''); setReasonError(null) } }}
        title="Reject Action"
        description="Please provide a reason for rejection."
        confirmLabel="Reject"
        variant="destructive"
        onConfirm={handleReject}
        loading={submitting}
      >
        <InputField
          multiline
          label="Reason for rejection"
          value={reason}
          onValueChange={(value) => {
            setReason(value)
            if (reasonError && value.trim()) setReasonError(null)
          }}
          placeholder="Give the requester enough context to iterate."
          rows={3}
          maxLength={2000}
          required
          autoFocus
          error={reasonError}
          className="mt-2"
        />
      </ConfirmDialog>
    </>
  )
}

function DescriptionSection({ approval }: { approval: ApprovalResponse }) {
  const isStripped = !!approval.metadata.stripped_description
  const displayText = approval.metadata.stripped_description || approval.description

  return (
    <div>
      <span className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Description
        {isStripped && (
          <span className="ml-1.5 text-[10px] font-normal normal-case text-warning">
            (PII redacted)
          </span>
        )}
      </span>
      <p className="mt-1 text-sm text-secondary">{displayText}</p>
    </div>
  )
}

function MetaField({ icon: Icon, label, value }: { icon: LucideIcon; label: string; value: string }) {
  return (
    <div className="flex items-start gap-2">
      <Icon className="mt-0.5 size-3.5 text-muted-foreground" aria-hidden="true" />
      <div>
        <span className="block text-[10px] text-muted-foreground">{label}</span>
        <span className="block text-xs text-foreground">{value}</span>
      </div>
    </div>
  )
}

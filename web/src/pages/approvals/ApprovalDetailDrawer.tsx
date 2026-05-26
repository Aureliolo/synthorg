import { useRef } from 'react'
import { motion } from 'motion/react'
import { AlertTriangle, Check, Loader2, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { springDefault, overlayBackdrop, tweenExitFast } from '@/lib/motion'
import { getApprovalStatusLabel, getRiskLevelLabel } from '@/utils/approvals'
import type { ApprovalResponse, ApproveRequest, RejectRequest } from '@/api/types/approvals'
import { ApprovalDetailContent } from './ApprovalDetailContent'
import {
  type ApprovalDecision,
  useApprovalDecision,
  useEscapeToClose,
  useFocusTrap,
  useRestoreFocusOnClose,
} from './useApprovalDrawer'

export interface ApprovalDetailDrawerProps {
  approval: ApprovalResponse | null
  open: boolean
  onClose: () => void
  /**
   * Resolve to ``true`` on success, ``false`` on failure. The drawer
   * uses the boolean to decide whether to close the confirmation dialog
   * and reset its inputs. Failure UX (toast / banner) is owned by the
   * caller's underlying store mutation -- the drawer does NOT try/catch.
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

function ApprovalDrawerHeader({
  approval,
  riskColor,
  onClose,
}: {
  approval: ApprovalResponse
  riskColor: string
  onClose: () => void
}) {
  return (
    <div className="flex items-center justify-between border-b border-border px-6 py-4">
      <div className="flex items-center gap-2">
        <span className={cn('size-2 rounded-full', RISK_DOT_CLASSES[riskColor])} aria-hidden="true" />
        <span
          className={cn(
            'inline-flex items-center rounded-full border px-1.5 py-0.5 text-[10px] font-medium leading-none',
            RISK_BADGE_CLASSES[riskColor],
          )}
        >
          {getRiskLevelLabel(approval.risk_level)}
        </span>
        <span className="text-xs text-text-secondary">{getApprovalStatusLabel(approval.status)}</span>
      </div>
      <Button variant="ghost" size="icon" onClick={onClose} aria-label="Close panel">
        <X className="size-4" />
      </Button>
    </div>
  )
}

function ApprovalDrawerFooter({ onApprove, onReject }: { onApprove: () => void; onReject: () => void }) {
  return (
    <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
      <Button
        size="sm"
        variant="outline"
        className="gap-1 border-success/30 text-success hover:bg-success/10"
        onClick={onApprove}
      >
        <Check className="size-3.5" />
        Approve
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
        onClick={onReject}
      >
        <X className="size-3.5" />
        Reject
      </Button>
    </div>
  )
}

function ApprovalDecisionDialogs({ decision }: { decision: ApprovalDecision }) {
  return (
    <>
      <ConfirmDialog
        open={decision.approveOpen}
        onOpenChange={(o) => {
          decision.setApproveOpen(o)
          if (!o) decision.setComment('')
        }}
        title="Approve Action"
        description="Are you sure you want to approve this action?"
        confirmLabel="Approve"
        onConfirm={decision.handleApprove}
        loading={decision.submitting}
      >
        <InputField
          multiline
          label="Optional comment"
          value={decision.comment}
          onValueChange={decision.setComment}
          placeholder="Add context for the requester..."
          rows={3}
          maxLength={2000}
          className="mt-2"
        />
      </ConfirmDialog>

      <ConfirmDialog
        open={decision.rejectOpen}
        onOpenChange={(o) => {
          decision.setRejectOpen(o)
          if (!o) {
            decision.setReason('')
            decision.setReasonError(null)
          }
        }}
        title="Reject Action"
        description="Please provide a reason for rejection."
        confirmLabel="Reject"
        variant="destructive"
        onConfirm={decision.handleReject}
        loading={decision.submitting}
      >
        <InputField
          multiline
          label="Reason for rejection"
          value={decision.reason}
          onValueChange={(value) => {
            decision.setReason(value)
            if (decision.reasonError && value.trim()) decision.setReasonError(null)
          }}
          placeholder="Give the requester enough context to iterate."
          rows={3}
          maxLength={2000}
          required
          autoFocus
          error={decision.reasonError}
          className="mt-2"
        />
      </ConfirmDialog>
    </>
  )
}

interface ApprovalDrawerPanelProps {
  approval: ApprovalResponse | null
  showLoadingState: boolean
  detailError: string | null | undefined
  decision: ApprovalDecision
  onClose: () => void
}

function ApprovalDrawerPanel({ approval, showLoadingState, detailError, decision, onClose }: ApprovalDrawerPanelProps) {
  if (showLoadingState && !detailError) {
    return (
      <div className="flex flex-1 items-center justify-center" role="status" aria-label="Loading approval">
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
    )
  }
  if (detailError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <AlertTriangle className="size-8 text-danger" />
        <p className="text-sm text-danger">{detailError}</p>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>
    )
  }
  if (!approval) return null
  return (
    <>
      <ApprovalDrawerHeader approval={approval} riskColor={decision.riskColor} onClose={onClose} />
      <ApprovalDetailContent approval={approval} confidenceLabel={decision.confidenceLabel} />
      {decision.isPending && (
        <ApprovalDrawerFooter
          onApprove={() => decision.setApproveOpen(true)}
          onReject={() => decision.setRejectOpen(true)}
        />
      )}
    </>
  )
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
  const decision = useApprovalDecision(approval, onApprove, onReject)
  const panelRef = useRef<HTMLElement>(null)

  useEscapeToClose(open, onClose, decision.approveOpen || decision.rejectOpen)
  useRestoreFocusOnClose(open)
  useFocusTrap(panelRef, open, `${String(loading)}-${approval?.id ?? ''}`)

  if (!open) return null

  return (
    <>
      <motion.div
        className="fixed inset-0 z-40 bg-background/60 backdrop-blur-sm"
        variants={overlayBackdrop}
        initial="initial"
        animate="animate"
        exit="exit"
        onClick={onClose}
      />

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
        <ApprovalDrawerPanel
          approval={approval}
          showLoadingState={loading || !approval}
          detailError={detailError}
          decision={decision}
          onClose={onClose}
        />
      </motion.aside>

      <ApprovalDecisionDialogs decision={decision} />
    </>
  )
}

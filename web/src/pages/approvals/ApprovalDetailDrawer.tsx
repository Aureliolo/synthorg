import type { ReactNode } from 'react'
import { AlertTriangle, Loader2 } from 'lucide-react'

import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { Drawer } from '@/components/ui/drawer'
import { InputField } from '@/components/ui/input-field'
import {
  DOT_COLOR_CLASSES,
  RISK_BADGE_CLASSES,
  getApprovalStatusLabel,
  getRiskLevelLabel,
  isFailedApproval,
} from '@/utils/approvals'
import type {
  ApprovalResponse,
  ApproveRequest,
  RejectRequest,
} from '@/api/types/approvals'
import { ApprovalDecisionButtons } from './ApprovalDecisionButtons'
import { ApprovalDetailContent } from './ApprovalDetailContent'
import { ApprovalRejectDialog } from './ApprovalRejectDialog'
import { type ApprovalDecision, useApprovalDecision } from './useApprovalDrawer'

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

function ApprovalDrawerHeader({
  approval,
  riskColor,
}: {
  approval: ApprovalResponse
  riskColor: keyof typeof DOT_COLOR_CLASSES
}) {
  return (
    <div className="flex items-center gap-2">
      <span
        className={cn('size-2 rounded-full', DOT_COLOR_CLASSES[riskColor])}
        aria-hidden="true"
      />
      <span
        className={cn(
          'inline-flex items-center rounded-full border px-1.5 py-0.5 text-micro font-medium leading-none',
          RISK_BADGE_CLASSES[riskColor],
        )}
      >
        {getRiskLevelLabel(approval.risk_level)}
      </span>
      <span className="text-xs text-text-secondary">
        {getApprovalStatusLabel(approval.status)}
      </span>
    </div>
  )
}

function ApprovalDecisionDialogs({
  decision,
  isFailed,
}: {
  decision: ApprovalDecision
  isFailed: boolean
}) {
  return (
    <>
      <ConfirmDialog
        open={decision.approveOpen}
        onOpenChange={(o) => {
          decision.setApproveOpen(o)
          if (!o) decision.setComment('')
        }}
        title={isFailed ? 'Acknowledge failure' : 'Approve Action'}
        description={
          isFailed
            ? 'Acknowledge this failed run and close it. The task stays failed.'
            : 'Are you sure you want to approve this action?'
        }
        confirmLabel={isFailed ? 'Acknowledge' : 'Approve'}
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

      <ApprovalRejectDialog decision={decision} isFailed={isFailed} />
    </>
  )
}

function ApprovalDrawerBody({
  approval,
  showLoadingState,
  detailError,
  confidenceLabel,
  onClose,
}: {
  approval: ApprovalResponse | null
  showLoadingState: boolean
  detailError: string | null | undefined
  confidenceLabel: string | null
  onClose: () => void
}) {
  if (showLoadingState && !detailError) {
    return (
      <div
        className="flex flex-1 items-center justify-center"
        role="status"
        aria-label="Loading approval"
      >
        <Loader2 className="size-6 animate-spin text-muted-foreground" aria-hidden="true" />
      </div>
    )
  }
  if (detailError) {
    return (
      <div className="flex flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <AlertTriangle className="size-8 text-danger" aria-hidden />
        <p className="text-sm text-danger">{detailError}</p>
        <Button variant="ghost" size="sm" onClick={onClose}>
          Close
        </Button>
      </div>
    )
  }
  if (!approval) return null
  return <ApprovalDetailContent approval={approval} confidenceLabel={confidenceLabel} />
}

/**
 * Build the drawer's optional `header` / `footer` slots. Extracted so the
 * component body stays under the complexity cap: the null/decidable branching
 * lives here and the drawer just spreads the result.
 */
function buildDrawerSlots(
  approval: ApprovalResponse | null,
  decision: ApprovalDecision,
  canDecide: boolean,
): { header?: ReactNode; footer?: ReactNode } {
  if (!approval) return {}
  const header = (
    <ApprovalDrawerHeader approval={approval} riskColor={decision.riskColor} />
  )
  if (!canDecide) return { header }
  return {
    header,
    footer: (
      <ApprovalDecisionButtons
        isFailed={isFailedApproval(approval)}
        onApprove={() => decision.setApproveOpen(true)}
        onReject={() => decision.setRejectOpen(true)}
        className="justify-end p-card"
      />
    ),
  }
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
  const showLoadingState = Boolean(loading) || !approval
  const canDecide = !showLoadingState && !detailError && decision.isPending
  const isFailed = approval !== null && isFailedApproval(approval)
  const slots = buildDrawerSlots(approval, decision, canDecide)

  return (
    <>
      <Drawer
        open={open}
        onClose={onClose}
        ariaLabel={approval ? `Approval detail: ${approval.title}` : 'Approval detail'}
        width="default"
        contentClassName="p-0"
        {...slots}
      >
        <ApprovalDrawerBody
          approval={approval}
          showLoadingState={showLoadingState}
          detailError={detailError}
          confidenceLabel={decision.confidenceLabel}
          onClose={onClose}
        />
      </Drawer>

      <ApprovalDecisionDialogs decision={decision} isFailed={isFailed} />
    </>
  )
}

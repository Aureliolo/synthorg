import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import type { ApprovalDecision } from './useApprovalDrawer'

export interface ApprovalRejectDialogProps {
  decision: ApprovalDecision
  isFailed: boolean
  /**
   * Called after the dialog fully closes (cancel or successful reject), on top
   * of the decision's own state reset. The list-level card-reject flow uses it
   * to clear the target approval so the same card can be reopened; the drawer
   * leaves it unset because its own approval selection owns that lifecycle.
   */
  onClosed?: () => void
}

/**
 * The reject-with-reason confirmation, shared by the detail drawer and the
 * list-level per-card Reject button so both collect a mandatory reason through
 * the same dialog rather than two divergent copies.
 */
export function ApprovalRejectDialog({ decision, isFailed, onClosed }: ApprovalRejectDialogProps) {
  return (
    <ConfirmDialog
      open={decision.rejectOpen}
      onOpenChange={(o) => {
        decision.setRejectOpen(o)
        if (!o) {
          decision.setReason('')
          decision.setReasonError(null)
          onClosed?.()
        }
      }}
      title={isFailed ? 'Retry task' : 'Reject Action'}
      description={
        isFailed
          ? 'Send this task back for rework. Explain what to change.'
          : 'Please provide a reason for rejection.'
      }
      confirmLabel={isFailed ? 'Retry' : 'Reject'}
      variant="destructive"
      onConfirm={decision.handleReject}
      loading={decision.submitting}
    >
      <InputField
        multiline
        label={isFailed ? 'Reason for rework' : 'Reason for rejection'}
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
  )
}

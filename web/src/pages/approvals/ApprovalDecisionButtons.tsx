import { Check, X } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

export interface ApprovalDecisionButtonsProps {
  /**
   * A failed run relabels the pair to "Acknowledge" / "Retry": approving a
   * failure acknowledges it (the task stays failed), rejecting requests rework.
   */
  isFailed: boolean
  onApprove: () => void
  onReject: () => void
  className?: string
}

/**
 * The Approve/Reject decision button pair, shared by the queue card and the
 * detail drawer footer so the two surfaces never drift on label, tone, or the
 * failed-run relabelling.
 */
export function ApprovalDecisionButtons({
  isFailed,
  onApprove,
  onReject,
  className,
}: ApprovalDecisionButtonsProps) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <Button
        size="sm"
        variant="outline"
        className="gap-1 border-success/30 text-success hover:bg-success/10"
        onClick={onApprove}
      >
        <Check className="size-3.5" aria-hidden />
        {isFailed ? 'Acknowledge' : 'Approve'}
      </Button>
      <Button
        size="sm"
        variant="outline"
        className="gap-1 border-danger/30 text-danger hover:bg-danger/10"
        onClick={onReject}
      >
        <X className="size-3.5" aria-hidden />
        {isFailed ? 'Retry' : 'Reject'}
      </Button>
    </div>
  )
}

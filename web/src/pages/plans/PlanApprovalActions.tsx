import { useCallback, useEffect, useMemo, useState } from 'react'

import { CheckCircle2, XCircle } from 'lucide-react'

import type { Plan } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { useApprovalsStore } from '@/stores/approvals'
import { usePlansStore } from '@/stores/plans'

// Mirror RejectRequest.reason's server bound so an over-long reason is capped
// in the browser rather than rejected after a round trip.
const REJECT_REASON_MAX = 2000
const APPROVAL_FETCH_LIMIT = 200

interface PlanApproval {
  approvalId: string | undefined
  submitting: boolean
  handleApprove: () => Promise<void>
  reject: RejectController
}

interface RejectController {
  open: boolean
  reason: string
  reasonError: string | null
  setOpen: (open: boolean) => void
  setReason: (reason: string) => void
  confirm: () => Promise<void>
}

function usePlanApproval(plan: Plan): PlanApproval {
  const approvals = useApprovalsStore((s) => s.approvals)

  // The always-mounted sidebar badge normally owns the approvals fetch; on a
  // direct deep-link to a plan it may not have run yet, so pull once if empty.
  useEffect(() => {
    if (useApprovalsStore.getState().approvals.length === 0) {
      void useApprovalsStore.getState().fetchApprovals({ limit: APPROVAL_FETCH_LIMIT })
    }
  }, [])

  const approvalId = useMemo(
    () =>
      approvals.find(
        (a) =>
          a.source === 'plan_review' &&
          a.status === 'pending' &&
          a.metadata['plan_id'] === plan.id,
      )?.id,
    [approvals, plan.id],
  )

  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)

  const refetchPlan = useCallback(() => {
    void usePlansStore.getState().fetchPlanDetail(plan.id)
  }, [plan.id])

  const handleApprove = useCallback(async () => {
    if (approvalId === undefined) return
    setSubmitting(true)
    const result = await useApprovalsStore.getState().approveOne(approvalId)
    setSubmitting(false)
    if (result) refetchPlan()
  }, [approvalId, refetchPlan])

  const confirm = useCallback(async () => {
    if (approvalId === undefined) return
    const trimmed = reason.trim()
    if (trimmed === '') {
      setReasonError('A reason is required.')
      return
    }
    setSubmitting(true)
    const result = await useApprovalsStore
      .getState()
      .rejectOne(approvalId, { reason: trimmed })
    setSubmitting(false)
    if (result) {
      setOpen(false)
      setReason('')
      refetchPlan()
    }
  }, [approvalId, reason, refetchPlan])

  const setReasonClearingError = useCallback(
    (value: string) => {
      setReason(value)
      if (value.trim()) setReasonError(null)
    },
    [],
  )

  return {
    approvalId,
    submitting,
    handleApprove,
    reject: {
      open,
      reason,
      reasonError,
      setOpen,
      setReason: setReasonClearingError,
      confirm,
    },
  }
}

function PlanRejectDialog({
  reject,
  submitting,
}: {
  reject: RejectController
  submitting: boolean
}) {
  return (
    <ConfirmDialog
      open={reject.open}
      onOpenChange={(open) => {
        reject.setOpen(open)
        if (!open) reject.setReason('')
      }}
      title="Reject plan"
      description="Reject this plan as a whole. Explain what is wrong so the organisation can iterate."
      confirmLabel="Reject"
      variant="destructive"
      onConfirm={() => void reject.confirm()}
      loading={submitting}
    >
      <InputField
        multiline
        label="Reason for rejection"
        value={reject.reason}
        onValueChange={reject.setReason}
        placeholder="Give the organisation enough context to iterate."
        rows={3}
        maxLength={REJECT_REASON_MAX}
        required
        autoFocus
        error={reject.reasonError}
        className="mt-2"
      />
    </ConfirmDialog>
  )
}

/**
 * Whole-plan approve / reject, inline on the Plan Review page.
 *
 * A plan review is decision-gathering with its own surface (#2593), so it no
 * longer appears in the generic Approvals inbox; the operator approves or
 * rejects the plan as a whole here. The decision still runs through the
 * canonical `/approvals` path (the plan's parked approval, resolved from its
 * `plan_id` metadata), so approval stays atomic and drives the same resume.
 * Renders nothing unless the plan is under review with a pending approval.
 */
export function PlanApprovalActions({ plan }: { plan: Plan }) {
  const { approvalId, submitting, handleApprove, reject } = usePlanApproval(plan)
  if (approvalId === undefined || plan.status !== 'pending_review') return null
  return (
    <>
      <Button size="sm" onClick={() => void handleApprove()} disabled={submitting}>
        <CheckCircle2 aria-hidden="true" />
        Approve plan
      </Button>
      <Button
        variant="outline"
        size="sm"
        onClick={() => reject.setOpen(true)}
        disabled={submitting}
      >
        <XCircle aria-hidden="true" />
        Reject
      </Button>
      <PlanRejectDialog reject={reject} submitting={submitting} />
    </>
  )
}

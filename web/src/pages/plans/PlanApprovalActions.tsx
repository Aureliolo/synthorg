import { type RefObject, useCallback, useEffect, useRef, useState } from 'react'

import { CheckCircle2, RefreshCw, XCircle } from 'lucide-react'

import { paginateAll } from '@/api/client'
import { listApprovals } from '@/api/endpoints/approvals'
import type { ApprovalResponse } from '@/api/types/approvals'
import type { Plan } from '@/api/types/plans'
import { Button } from '@/components/ui/button'
import { ConfirmDialog } from '@/components/ui/confirm-dialog'
import { InputField } from '@/components/ui/input-field'
import { createLogger } from '@/lib/logger'
import { useApprovalsStore } from '@/stores/approvals'
import { usePlansStore } from '@/stores/plans'
import { sanitizeForLog } from '@/utils/logging'

const log = createLogger('plan-approval-actions')

// Mirror RejectRequest.reason's server bound so an over-long reason is capped
// in the browser rather than rejected after a round trip.
const REJECT_REASON_MAX = 2000
// One page of PENDING PLAN REVIEWS (a small, bounded set for a single operator),
// scoped by ``source`` so the plan's parked approval is found regardless of how
// many unrelated approvals are outstanding.
const PENDING_REVIEW_FETCH_LIMIT = 200
// Mirrors PLAN_APPROVAL_ACTION_TYPE (approval/plan_review.py): the action type
// the plan-approval gate parks under, which is what separates it from the
// clarify questions parked beside it on the same plan.
const PLAN_APPROVE_ACTION = 'plan:approve'

interface PlanApproval {
  approvalId: string | undefined
  submitting: boolean
  /** True when the last approval lookup failed, so the UI can offer a retry. */
  lookupFailed: boolean
  handleApprove: () => Promise<void>
  retry: () => Promise<void>
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

// Resolve the plan's parked approval from the pending plan reviews (scoped by
// ``source``, walked in full via paginateAll), not the generic approvals page:
// with many reviews outstanding, this plan's review could otherwise fall
// outside a single page and the approve controls would vanish.
//
// The action type is half the key, not decoration. The gate parks a
// `clarify:question` per unresolved plan question under the SAME source and
// the SAME `plan_id`, so matching on the plan alone returns whichever the API
// happened to order first: a live run approved a parked question while the
// plan stayed pending, and the audit recorded the operator as having decided a
// question they were never shown.
async function findPendingPlanApproval(planId: string): Promise<string | undefined> {
  const reviews = await paginateAll<ApprovalResponse>((cursor) =>
    listApprovals({
      source: 'plan_review',
      status: 'pending',
      limit: PENDING_REVIEW_FETCH_LIMIT,
      cursor,
    }),
  )
  return reviews.find(
    (a) => a.metadata['plan_id'] === planId && a.action_type === PLAN_APPROVE_ACTION,
  )?.id
}

function usePlanApproval(plan: Plan): PlanApproval {
  const [approvalId, setApprovalId] = useState<string | undefined>(undefined)
  const [lookupFailed, setLookupFailed] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  // Monotonic plan generation: bumped on every (re)lookup, i.e. whenever the
  // plan changes. Both the lookup and the approve/reject mutations capture it
  // and drop a completion once it no longer matches, so a late response can
  // never resume -- or clear the controls of -- the wrong plan.
  const generationRef = useRef(0)

  const resolveApproval = useCallback(async () => {
    const generation = (generationRef.current += 1)
    // Invalidate the previous plan's transient state immediately so stale
    // controls / spinners / errors never show while the new lookup is in flight.
    setApprovalId(undefined)
    setLookupFailed(false)
    setSubmitting(false)
    try {
      const id = await findPendingPlanApproval(plan.id)
      if (generation !== generationRef.current) return
      setApprovalId(id)
    } catch (err) {
      if (generation !== generationRef.current) return
      log.error('Failed to resolve the plan approval', sanitizeForLog(err))
      setApprovalId(undefined)
      // Surface the failure so the operator gets a retry affordance rather than
      // silently-vanished approve/reject controls on this core review flow.
      setLookupFailed(true)
    }
  }, [plan.id])

  // Re-resolve when the plan first enters review as well as on mount, so the
  // controls appear as soon as its approval is parked.
  useEffect(() => {
    void resolveApproval()
  }, [resolveApproval, plan.status])

  const refetchPlan = useCallback(() => {
    void usePlansStore.getState().fetchPlanDetail(plan.id)
  }, [plan.id])

  const onResolved = useCallback(() => {
    setApprovalId(undefined)
    refetchPlan()
  }, [refetchPlan])

  const handleApprove = useCallback(async () => {
    if (approvalId === undefined) return
    const generation = generationRef.current
    setSubmitting(true)
    const result = await useApprovalsStore.getState().approveOne(approvalId)
    // Drop the completion if the operator has since navigated to another plan;
    // otherwise plan A's resolve would clear plan B's controls.
    if (generation !== generationRef.current) return
    setSubmitting(false)
    if (result) onResolved()
  }, [approvalId, onResolved])

  const reject = useRejectController({
    approvalId,
    submitting,
    setSubmitting,
    onResolved,
    generationRef,
  })

  return {
    approvalId,
    submitting,
    lookupFailed,
    handleApprove,
    retry: resolveApproval,
    reject,
  }
}

/** Reason box + validation + submit for the whole-plan reject dialog. */
function useRejectController(args: {
  approvalId: string | undefined
  submitting: boolean
  setSubmitting: (value: boolean) => void
  onResolved: () => void
  generationRef: RefObject<number>
}): RejectController {
  const { approvalId, setSubmitting, onResolved, generationRef } = args
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const [reasonError, setReasonError] = useState<string | null>(null)

  const confirm = useCallback(async () => {
    if (approvalId === undefined) return
    const trimmed = reason.trim()
    if (trimmed === '') {
      setReasonError('A reason is required.')
      return
    }
    const generation = generationRef.current
    setSubmitting(true)
    const result = await useApprovalsStore
      .getState()
      .rejectOne(approvalId, { reason: trimmed })
    // Drop the completion if the operator has navigated to another plan while
    // the reject was in flight, so it cannot clear the new plan's controls.
    if (generation !== generationRef.current) return
    setSubmitting(false)
    if (result) {
      setOpen(false)
      setReason('')
      setReasonError(null)
      onResolved()
    }
  }, [approvalId, reason, setSubmitting, onResolved, generationRef])

  const setReasonClearingError = useCallback((value: string) => {
    setReason(value)
    if (value.trim()) setReasonError(null)
  }, [])

  // Closing the dialog clears BOTH the reason and its validation error, so a
  // dialog reopened after an empty-submit does not show a stale error.
  const setRejectOpen = useCallback((next: boolean) => {
    setOpen(next)
    if (!next) {
      setReason('')
      setReasonError(null)
    }
  }, [])

  return {
    open,
    reason,
    reasonError,
    setOpen: setRejectOpen,
    setReason: setReasonClearingError,
    confirm,
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
      onOpenChange={reject.setOpen}
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
 * A plan review is decision-gathering with its own surface, so it does not
 * appear in the generic Approvals inbox; the operator approves or rejects the
 * plan as a whole here. The decision still runs through the canonical
 * `/approvals` path (the plan's parked approval, resolved from its `plan_id`
 * metadata), so approval stays atomic and drives the same resume.
 *
 * The parked approval alone decides whether there is anything to decide. The
 * plan's status is not a second answer to that: a plan can leave
 * `pending_review` with its approval still parked (a resume, a supersede), and
 * gating on the status then hid the one control the whole feature exists to
 * offer while the backend waited on it forever.
 */
export function PlanApprovalActions({ plan }: { plan: Plan }) {
  const { approvalId, submitting, lookupFailed, handleApprove, retry, reject } =
    usePlanApproval(plan)
  if (approvalId === undefined) {
    // A transient lookup failure would otherwise hide the approve/reject
    // controls with no recourse; offer an explicit retry instead.
    if (!lookupFailed) return null
    return (
      <Button variant="outline" size="sm" onClick={() => void retry()}>
        <RefreshCw aria-hidden="true" />
        Retry loading approval
      </Button>
    )
  }
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

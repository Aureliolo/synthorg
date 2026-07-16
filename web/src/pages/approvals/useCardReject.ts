import { useCallback, useEffect, useState } from 'react'

import type { ApprovalResponse, RejectRequest } from '@/api/types/approvals'
import { isFailedApproval } from '@/utils/approvals'

import { type ApprovalDecision, useApprovalDecision } from './useApprovalDrawer'

// Approving from a card is a one-click action that never opens this dialog, so
// the decision hook's approve path is wired to a stable no-op here.
const NOOP_APPROVE = (): Promise<boolean> => Promise.resolve(false)

export interface CardReject {
  /** The approval whose reject dialog is open, or null when closed. */
  target: ApprovalResponse | null
  decision: ApprovalDecision
  isFailed: boolean
  /** Open the reject-with-reason dialog for the given approval id. */
  openReject: (id: string) => void
  /** Clear the target once the dialog fully closes. */
  clearTarget: () => void
}

/**
 * Drives the list-level per-card Reject dialog. The card's Reject button opens
 * a reject-with-reason dialog directly (mirroring one-click Approve) instead of
 * detouring through the detail drawer. Reuses {@link useApprovalDecision} so
 * the reason field, validation, and submit state match the drawer exactly.
 */
export function useCardReject(
  approvals: readonly ApprovalResponse[],
  onReject: (id: string, data: RejectRequest) => Promise<boolean>,
): CardReject {
  const [targetId, setTargetId] = useState<string | null>(null)
  const target =
    targetId != null ? (approvals.find((a) => a.id === targetId) ?? null) : null

  // Clear the target on a successful reject so the decision hook's id-change
  // reset closes the dialog and the same card can be reopened later.
  const rejectAndClear = useCallback(
    async (id: string, data: RejectRequest): Promise<boolean> => {
      const ok = await onReject(id, data)
      if (ok) setTargetId(null)
      return ok
    },
    [onReject],
  )

  const decision = useApprovalDecision(target, NOOP_APPROVE, rejectAndClear)
  const { setRejectOpen } = decision

  // Open the dialog once a fresh target is set. This runs after commit, so it
  // wins over the in-render id-change reset inside useApprovalDecision that
  // would otherwise leave the newly-targeted dialog closed.
  useEffect(() => {
    if (targetId !== null) setRejectOpen(true)
  }, [targetId, setRejectOpen])

  const openReject = useCallback((id: string) => setTargetId(id), [])
  const clearTarget = useCallback(() => setTargetId(null), [])

  return {
    target,
    decision,
    isFailed: target !== null && isFailedApproval(target),
    openReject,
    clearTarget,
  }
}

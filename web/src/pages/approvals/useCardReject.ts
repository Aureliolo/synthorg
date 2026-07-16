import { useCallback, useState } from 'react'

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
 * the reason field, validation, and submit state match the drawer exactly; the
 * `openRejectOnTargetChange` flag makes a fresh target open the dialog
 * synchronously. The dialog's close (cancel or successful reject, both routed
 * through ConfirmDialog -> onClosed) is the sole owner of clearing the target.
 */
export function useCardReject(
  approvals: readonly ApprovalResponse[],
  onReject: (id: string, data: RejectRequest) => Promise<boolean>,
): CardReject {
  const [targetId, setTargetId] = useState<string | null>(null)
  const target =
    targetId != null ? (approvals.find((a) => a.id === targetId) ?? null) : null

  const decision = useApprovalDecision(target, NOOP_APPROVE, onReject, true)

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

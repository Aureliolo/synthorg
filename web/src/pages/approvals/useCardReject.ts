import { useCallback, useState } from 'react'

import type { ApprovalResponse, RejectRequest } from '@/api/types/approvals'
import { isFailedApproval } from '@/utils/approvals'

import {
  type ApprovalDecision,
  useApprovalDecision,
  useResetOnChange,
} from './useApprovalDrawer'

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
}

/**
 * Drives the list-level per-card Reject dialog. The card's Reject button opens
 * a reject-with-reason dialog directly (mirroring one-click Approve) instead of
 * detouring through the detail drawer. Reuses {@link useApprovalDecision} so
 * the reason field, validation, and submit state match the drawer exactly; the
 * `openRejectOnTargetChange` flag makes a fresh target open the dialog
 * synchronously.
 */
export function useCardReject(
  approvals: readonly ApprovalResponse[],
  onReject: (id: string, data: RejectRequest) => Promise<boolean>,
): CardReject {
  const [targetId, setTargetId] = useState<string | null>(null)
  const target =
    targetId != null ? (approvals.find((a) => a.id === targetId) ?? null) : null

  const decision = useApprovalDecision(target, NOOP_APPROVE, onReject, true)

  // Clear the target whenever the reject dialog closes, however it closed. A
  // user cancel or a successful reject routes through ConfirmDialog, but a
  // programmatic close (the approval leaves `pending` or drops off the list via
  // a WebSocket update) flips `rejectOpen` inside useApprovalDecision with no
  // onOpenChange. This render-phase sync is the single owner that keeps
  // `targetId` from going stale and blocking a later reopen of the same card.
  useResetOnChange(decision.rejectOpen, () => {
    if (!decision.rejectOpen) setTargetId(null)
  })

  const openReject = useCallback((id: string) => setTargetId(id), [])

  return {
    target,
    decision,
    isFailed: target !== null && isFailedApproval(target),
    openReject,
  }
}

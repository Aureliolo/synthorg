import { createLogger } from '@/lib/logger'
import type { ApprovalResponse } from '@/api/types/approvals'
import { pendingTransitions } from './_state'
import type { ApprovalsGet, ApprovalsSet, ApprovalsState } from './types'

const log = createLogger('approvals')

type ApprovalStatus = 'approved' | 'rejected'

interface OptimisticSnapshot {
  oldApproval: ApprovalResponse
  hadSelection: boolean
}

function applyOptimisticTransition(
  set: ApprovalsSet,
  get: ApprovalsGet,
  id: string,
  newStatus: ApprovalStatus,
): OptimisticSnapshot | null {
  const approvals = get().approvals
  const idx = approvals.findIndex((a) => a.id === id)
  if (idx === -1) {
    log.warn(`optimistic${newStatus}: approval not found in store`, id)
    return null
  }
  pendingTransitions.add(id)
  const prevSelectedIds = get().selectedIds
  const hadSelection = prevSelectedIds.has(id)
  const newSelectedIds = new Set(prevSelectedIds)
  newSelectedIds.delete(id)
  const oldApproval = approvals[idx]!
  const updated: ApprovalResponse = {
    ...oldApproval,
    status: newStatus,
    decided_at: new Date().toISOString(),
  }
  const newApprovals = [...approvals]
  newApprovals[idx] = updated
  const selectedApproval = get().selectedApproval?.id === id
    ? updated
    : get().selectedApproval
  set({
    approvals: newApprovals,
    selectedIds: newSelectedIds,
    selectedApproval,
  })
  return { oldApproval, hadSelection }
}

function buildRollback(
  set: ApprovalsSet,
  id: string,
  snapshot: OptimisticSnapshot,
): () => void {
  return () => {
    pendingTransitions.delete(id)
    set((s) => {
      const currentApprovals = [...s.approvals]
      const currentIdx = currentApprovals.findIndex((a) => a.id === id)
      if (currentIdx !== -1) {
        currentApprovals[currentIdx] = snapshot.oldApproval
      }
      const restoredIds = snapshot.hadSelection
        ? new Set([...s.selectedIds, id])
        : s.selectedIds
      const restoredSelected = s.selectedApproval?.id === id
        ? snapshot.oldApproval
        : s.selectedApproval
      return {
        approvals: currentApprovals,
        selectedIds: restoredIds,
        selectedApproval: restoredSelected,
      }
    })
  }
}

function upsertApproval(set: ApprovalsSet, approval: ApprovalResponse): void {
  pendingTransitions.delete(approval.id)
  set((s) => {
    const idx = s.approvals.findIndex((a) => a.id === approval.id)
    const newApprovals = idx === -1
      ? [approval, ...s.approvals]
      : [...s.approvals]
    if (idx !== -1) newApprovals[idx] = approval
    const selectedApproval = s.selectedApproval?.id === approval.id
      ? approval
      : s.selectedApproval
    const newSelectedIds = approval.status !== 'pending'
      && s.selectedIds.has(approval.id)
      ? new Set([...s.selectedIds].filter((sid) => sid !== approval.id))
      : s.selectedIds
    const patch: Partial<ApprovalsState> = {
      approvals: newApprovals,
      selectedApproval,
      selectedIds: newSelectedIds,
    }
    if (idx === -1) patch.total = s.total + 1
    return patch
  })
}

export function createOptimisticActions(
  set: ApprovalsSet,
  get: ApprovalsGet,
) {
  return {
    optimisticApprove(id: string): () => void {
      const snapshot = applyOptimisticTransition(set, get, id, 'approved')
      if (!snapshot) return () => {}
      return buildRollback(set, id, snapshot)
    },

    optimisticReject(id: string): () => void {
      const snapshot = applyOptimisticTransition(set, get, id, 'rejected')
      if (!snapshot) return () => {}
      return buildRollback(set, id, snapshot)
    },

    upsertApproval(approval: ApprovalResponse): void {
      upsertApproval(set, approval)
    },
  }
}

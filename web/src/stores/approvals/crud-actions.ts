import * as approvalsApi from '@/api/endpoints/approvals'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  ApprovalFilters,
  ApprovalResponse,
  ApproveRequest,
  RejectRequest,
} from '@/api/types/approvals'
import {
  getDetailRequestSeq,
  getListRequestSeq,
  getRequestEpoch,
  nextDetailRequestSeq,
  nextListRequestSeq,
  pendingTransitions,
} from './_state'
import type { ApprovalsGet, ApprovalsSet } from './types'

const log = createLogger('approvals')

function mergePreservingOptimistic(
  serverData: readonly ApprovalResponse[],
  get: ApprovalsGet,
): ApprovalResponse[] {
  return serverData.map((serverItem) => {
    if (pendingTransitions.has(serverItem.id)) {
      const existing = get().approvals.find((a) => a.id === serverItem.id)
      return existing ?? serverItem
    }
    return serverItem
  })
}

function prunePendingSelection(
  selectedIds: ReadonlySet<string>,
  merged: readonly ApprovalResponse[],
): Set<string> | ReadonlySet<string> {
  const pendingIds = new Set(
    merged.filter((a) => a.status === 'pending').map((a) => a.id),
  )
  const hasStale = [...selectedIds].some((sid) => !pendingIds.has(sid))
  return hasStale
    ? new Set([...selectedIds].filter((sid) => pendingIds.has(sid)))
    : selectedIds
}

async function fetchApprovalsImpl(
  set: ApprovalsSet,
  get: ApprovalsGet,
  filters?: ApprovalFilters,
): Promise<void> {
  const epoch = getRequestEpoch()
  const seq = nextListRequestSeq()
  set({ loading: true, error: null })
  try {
    const result = await approvalsApi.listApprovals(filters)
    if (epoch !== getRequestEpoch() || seq !== getListRequestSeq()) return
    const merged = mergePreservingOptimistic(result.data, get)
    const prunedSelected = prunePendingSelection(get().selectedIds, merged)
    const currentSelected = get().selectedApproval
    const freshSelected = currentSelected
      ? merged.find((a) => a.id === currentSelected.id) ?? currentSelected
      : null
    set({
      approvals: merged,
      total: merged.length,
      loading: false,
      // ``prunedSelected`` is a ReadonlySet when prunePendingSelection
      // returns the input unchanged; copy into a fresh mutable Set so
      // downstream code can never accidentally mutate shared state.
      selectedIds: new Set(prunedSelected),
      selectedApproval: freshSelected,
    })
  } catch (err) {
    if (epoch !== getRequestEpoch() || seq !== getListRequestSeq()) return
    log.warn('Failed to fetch approvals', sanitizeForLog(err))
    set({ loading: false, error: getErrorMessage(err) })
  }
}

async function fetchApprovalImpl(
  set: ApprovalsSet,
  id: string,
): Promise<void> {
  const epoch = getRequestEpoch()
  const seq = nextDetailRequestSeq()
  set({
    loadingDetail: true,
    detailError: null,
    selectedApproval: null,
  })
  try {
    const approval = await approvalsApi.getApproval(id)
    if (epoch !== getRequestEpoch() || seq !== getDetailRequestSeq()) return
    set({
      selectedApproval: approval,
      loadingDetail: false,
      detailError: null,
    })
  } catch (err) {
    if (epoch !== getRequestEpoch() || seq !== getDetailRequestSeq()) return
    log.warn('Failed to fetch approval detail', sanitizeForLog(err))
    set({ loadingDetail: false, detailError: getErrorMessage(err) })
  }
}

async function approveOneImpl(
  get: ApprovalsGet,
  id: string,
  data?: ApproveRequest,
): Promise<ApprovalResponse | null> {
  try {
    const approval = await approvalsApi.approveApproval(id, data)
    get().upsertApproval(approval)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Approval granted',
    })
    return approval
  } catch (err) {
    log.error('Approve approval failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not approve'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function rejectOneImpl(
  get: ApprovalsGet,
  id: string,
  data: RejectRequest,
): Promise<ApprovalResponse | null> {
  try {
    const approval = await approvalsApi.rejectApproval(id, data)
    get().upsertApproval(approval)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Approval rejected',
    })
    return approval
  } catch (err) {
    log.error('Reject approval failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Could not reject'),
      description: getErrorMessage(err),
    })
    return null
  }
}

export function createCrudActions(set: ApprovalsSet, get: ApprovalsGet) {
  return {
    fetchApprovals: (filters?: ApprovalFilters) =>
      fetchApprovalsImpl(set, get, filters),
    fetchApproval: (id: string) => fetchApprovalImpl(set, id),
    approveOne: (id: string, data?: ApproveRequest) =>
      approveOneImpl(get, id, data),
    rejectOne: (id: string, data: RejectRequest) =>
      rejectOneImpl(get, id, data),
  }
}

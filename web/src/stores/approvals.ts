import { create } from 'zustand'
import {
  _resetDetailRequestSeq,
  _resetPendingTransitions,
  bumpRequestEpoch,
  pendingTransitions,
  resetListRequestSeq,
} from './approvals/_state'
import { createBatchActions } from './approvals/batch-actions'
import { createCrudActions } from './approvals/crud-actions'
import { createOptimisticActions } from './approvals/optimistic-actions'
import { createSelectionActions } from './approvals/selection-actions'
import { createWsHandler } from './approvals/ws-handler'
import type { ApprovalsState } from './approvals/types'

export type { ApprovalsState } from './approvals/types'
export { _resetPendingTransitions }

export const useApprovalsStore = create<ApprovalsState>()((set, get) => ({
  approvals: [],
  selectedApproval: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  detailError: null,
  pendingTransitions,
  selectedIds: new Set<string>(),

  ...createCrudActions(set, get),
  ...createOptimisticActions(set, get),
  ...createWsHandler(get),
  ...createSelectionActions(set),
  ...createBatchActions(get),

  dispose() {
    // Bump the generation token so any in-flight request from
    // before the dispose can never collide with post-dispose seq
    // values (the captured ``epoch`` will not match the new
    // ``requestEpoch``). Resetting the seq counters and the
    // optimistic-transition set keeps fresh calls starting from
    // clean state.
    bumpRequestEpoch()
    _resetPendingTransitions()
    _resetDetailRequestSeq()
    resetListRequestSeq()
  },
}))

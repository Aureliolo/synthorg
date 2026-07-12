import { create } from 'zustand'

import { createDetailActions } from './plans/detail-actions'
import { createListActions } from './plans/list-actions'
import type { PlansState } from './plans/types'
import { createWsHandler } from './plans/ws-handler'

export type { PlansState } from './plans/types'

export const usePlansStore = create<PlansState>()((set, get) => ({
  plans: [],
  listLoading: false,
  listError: null,
  statusFilter: null,
  selectedPlan: null,
  detailLoading: false,
  detailError: null,
  parentTaskTitle: null,
  ...createListActions(set),
  ...createDetailActions(set),
  ...createWsHandler(get),
}))

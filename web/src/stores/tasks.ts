import { create } from 'zustand'
import { pendingTransitions } from './tasks/_state'
import { createCrudActions } from './tasks/crud-actions'
import { createOptimisticActions } from './tasks/optimistic-actions'
import { createWsHandler } from './tasks/ws-handler'
import type { TasksState } from './tasks/types'

export type { TasksState } from './tasks/types'

export const useTasksStore = create<TasksState>()((set, get) => ({
  tasks: [],
  selectedTask: null,
  total: 0,
  loading: false,
  loadingDetail: false,
  error: null,
  pendingTransitions,

  ...createCrudActions(set, get),
  ...createOptimisticActions(set, get),
  ...createWsHandler(get),
}))

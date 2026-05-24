import { create } from 'zustand'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { createCrudActions } from './workflows/crud-actions'
import { createListActions } from './workflows/list-actions'
import type { WorkflowsState } from './workflows/types'

export type { WorkflowsState } from './workflows/types'

const log = createLogger('workflows')

export const useWorkflowsStore = create<WorkflowsState>()((set, get) => ({
  workflows: [],
  totalWorkflows: 0,
  nextCursor: null,
  hasMore: false,
  listLoading: false,
  listLoadingMore: false,
  listError: null,

  blueprints: [],
  blueprintsLoading: false,
  blueprintsError: null,

  searchQuery: '',
  workflowTypeFilter: null,

  ...createListActions(set, get),
  ...createCrudActions(set, get),

  updateFromWsEvent: () => {
    get().fetchWorkflows().catch((err: unknown) => {
      log.warn('workflows ws refetch failed', sanitizeForLog(err))
    })
  },
}))

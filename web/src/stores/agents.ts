import { create } from 'zustand'
import { createDetailActions } from './agents/detail-actions'
import { createListActions } from './agents/list-actions'
import { createWsHandler } from './agents/ws-handler'
import type { AgentsState } from './agents/types'

export type { AgentsState } from './agents/types'

export const useAgentsStore = create<AgentsState>()((set, get) => ({
  // List page defaults
  agents: [],
  totalAgents: 0,
  listLoading: false,
  listError: null,

  // Filter defaults
  searchQuery: '',
  departmentFilter: null,
  levelFilter: null,
  statusFilter: null,
  sortBy: 'name',
  sortDirection: 'asc',

  // Detail page defaults
  selectedAgent: null,
  performance: null,
  agentTasks: [],
  activity: [],
  activityTotal: 0,
  activityNextCursor: null,
  activityHasMore: false,
  activityLoading: false,
  careerHistory: [],
  detailLoading: false,
  detailError: null,

  // Runtime statuses
  runtimeStatuses: {},

  ...createListActions(set),
  ...createDetailActions(set, get),
  ...createWsHandler(set),
}))

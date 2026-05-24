import { create } from 'zustand'
import { createCrudActions } from './projects/crud-actions'
import { createDetailActions } from './projects/detail-actions'
import { createListActions } from './projects/list-actions'
import { createWsHandler } from './projects/ws-handler'
import type { ProjectsState } from './projects/types'

export type { ProjectsState } from './projects/types'

export const useProjectsStore = create<ProjectsState>()((set, get) => ({
  projects: [],
  totalProjects: 0,
  listLoading: false,
  listError: null,

  searchQuery: '',
  statusFilter: null,
  leadFilter: null,

  selectedProject: null,
  projectTasks: [],
  detailLoading: false,
  detailError: null,

  ...createListActions(set),
  ...createDetailActions(set),
  ...createCrudActions(set, get),
  ...createWsHandler(set, get),
}))

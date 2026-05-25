import type { StoreApi } from 'zustand'
import type {
  CreateProjectRequest,
  Project,
  ProjectStatus,
  Task,
  WsEvent,
} from '@/api/types'

export interface BatchDeleteOutcome {
  succeeded: number
  failed: number
  failedReasons: string[]
}

export interface ProjectsState {
  // List page
  projects: readonly Project[]
  /** Opaque cursor for the next page; null on the final page. */
  nextCursor: string | null
  /** Whether more items follow the current page. */
  hasMore: boolean
  listLoading: boolean
  listError: string | null

  // Filters
  searchQuery: string
  statusFilter: ProjectStatus | null
  leadFilter: string | null

  // Detail page
  selectedProject: Project | null
  projectTasks: readonly Task[]
  detailLoading: boolean
  detailError: string | null

  // Actions. Mutations follow the canonical store error contract:
  // log + error toast + return sentinel (`null`) on failure.
  fetchProjects: () => Promise<void>
  fetchMoreProjects: () => Promise<void>
  fetchProjectDetail: (id: string) => Promise<void>
  createProject: (data: CreateProjectRequest) => Promise<Project | null>
  deleteProject: (id: string) => Promise<boolean>
  batchDeleteProjects: (
    ids: readonly string[],
  ) => Promise<BatchDeleteOutcome | false>
  setSearchQuery: (q: string) => void
  setStatusFilter: (s: ProjectStatus | null) => void
  setLeadFilter: (l: string | null) => void
  clearDetail: () => void
  updateFromWsEvent: (event: WsEvent) => void
}

export type ProjectsSet = StoreApi<ProjectsState>['setState']
export type ProjectsGet = StoreApi<ProjectsState>['getState']

import type { StoreApi } from 'zustand'
import type { AutonomyLevel, ProjectStatus } from '@/api/types/enums'
import type {
  CreateProjectRequest,
  Project,
  ProjectProgress,
} from '@/api/types/projects'
import type { Task } from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'

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
  /** Plan + task progress for the selected project; null before it loads. */
  projectProgress: ProjectProgress | null
  /**
   * True when the progress fetch failed, as opposed to the project genuinely
   * having no plan. Both leave `projectProgress` null, and the two must not
   * render the same way.
   */
  projectProgressFailed: boolean
  detailLoading: boolean
  detailError: string | null
  /** True while an autonomy-mode PATCH is in flight (disables the control). */
  autonomyModeSaving: boolean

  // Actions. Mutations follow the canonical store error contract:
  // log + error toast + return sentinel (`null`) on failure.
  fetchProjects: () => Promise<void>
  fetchMoreProjects: () => Promise<void>
  fetchProjectDetail: (id: string) => Promise<void>
  createProject: (data: CreateProjectRequest) => Promise<Project | null>
  setAutonomyMode: (
    id: string,
    mode: AutonomyLevel | null,
    confirm?: boolean,
  ) => Promise<Project | null>
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

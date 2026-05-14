import { create } from 'zustand'
import {
  createProject as createProjectApi,
  deleteProject as deleteProjectApi,
  getProject,
  listProjects,
} from '@/api/endpoints/projects'
import { listTasks } from '@/api/endpoints/tasks'
import { formatBatchErrors, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { sanitizeWsString } from '@/stores/notifications'
import { useToastStore } from '@/stores/toast'
import type { ProjectStatus } from '@/api/types/enums'
import type { CreateProjectRequest, Project } from '@/api/types/projects'
import type { Task } from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('projects')

interface ProjectsState {
  // List page
  projects: readonly Project[]
  totalProjects: number
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
  // log + error toast + return sentinel (`null`) on failure. Callers
  // MUST NOT wrap these in try/catch.
  fetchProjects: () => Promise<void>
  fetchProjectDetail: (id: string) => Promise<void>
  createProject: (data: CreateProjectRequest) => Promise<Project | null>
  deleteProject: (id: string) => Promise<boolean>
  batchDeleteProjects: (
    ids: readonly string[],
  ) => Promise<
    | { succeeded: number; failed: number; failedReasons: string[] }
    | false
  >
  setSearchQuery: (q: string) => void
  setStatusFilter: (s: ProjectStatus | null) => void
  setLeadFilter: (l: string | null) => void
  clearDetail: () => void
  updateFromWsEvent: (event: WsEvent) => void
}

let _detailRequestToken = 0
/** True when a newer detail request has superseded this one. */
function isStaleDetailRequest(token: number): boolean { return _detailRequestToken !== token }

let _listRequestToken = 0
/** True when a newer list request has superseded this one. */
function isStaleListRequest(token: number): boolean { return _listRequestToken !== token }

export const useProjectsStore = create<ProjectsState>()((set) => ({
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

  fetchProjects: async () => {
    const token = ++_listRequestToken
    set({ listLoading: true, listError: null })
    try {
      const result = await listProjects({ limit: 200 })
      if (isStaleListRequest(token)) return
      set({ projects: result.data, totalProjects: result.data.length, listLoading: false })
    } catch (err) {
      if (isStaleListRequest(token)) return
      set({ listLoading: false, listError: getErrorMessage(err) })
    }
  },

  fetchProjectDetail: async (id: string) => {
    const token = ++_detailRequestToken
    set({ detailLoading: true, detailError: null, selectedProject: null, projectTasks: [] })

    const [projectResult, tasksResult] = await Promise.allSettled([
      getProject(id),
      listTasks({ project: id, limit: 50 }),
    ])

    if (isStaleDetailRequest(token)) return

    const project = projectResult.status === 'fulfilled' ? projectResult.value : null
    if (!project) {
      const reason = projectResult.status === 'rejected' ? projectResult.reason : null
      set({ detailLoading: false, detailError: getErrorMessage(reason ?? 'Project not found'), selectedProject: null })
      return
    }

    const partialErrors: string[] = []
    if (tasksResult.status === 'rejected') partialErrors.push(`tasks: ${getErrorMessage(tasksResult.reason)}`)

    set({
      selectedProject: project,
      projectTasks: tasksResult.status === 'fulfilled' ? tasksResult.value.data : [],
      detailLoading: false,
      detailError: partialErrors.length > 0
        ? `Some data failed to load: ${partialErrors.join(', ')}. Displayed data may be incomplete.`
        : null,
    })
  },

  createProject: async (data: CreateProjectRequest) => {
    try {
      const project = await createProjectApi(data)
      // Optimistically add to local state for immediate UI update.
      // Filter by ID first to prevent duplicates if a concurrent fetch already added it.
      set((state) => {
        const exists = state.projects.some((p) => p.id === project.id)
        const filtered = state.projects.filter((p) => p.id !== project.id)
        return {
          projects: [project, ...filtered],
          totalProjects: exists ? state.totalProjects : state.totalProjects + 1,
        }
      })
      useToastStore.getState().add({
        variant: 'success',
        title: `Project ${project.name} created`,
      })
      // Polling and WS events will reconcile with server state.
      return project
    } catch (err) {
      log.error('Create project failed:', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to create project',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  deleteProject: async (id: string) => {
    // Capture only the specific row we optimistically remove. Restoring
    // a full snapshot on failure could resurrect projects that were
    // legitimately deleted by a concurrent request or WS update.
    const removedProject =
      useProjectsStore.getState().projects.find((p) => p.id === id) ?? null
    set((state) => {
      const filtered = state.projects.filter((p) => p.id !== id)
      return {
        projects: filtered,
        totalProjects: state.projects.length !== filtered.length
          ? Math.max(0, state.totalProjects - 1)
          : state.totalProjects,
      }
    })
    try {
      await deleteProjectApi(id)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Project deleted',
      })
      return true
    } catch (err) {
      log.error('Delete project failed:', sanitizeForLog(err))
      if (removedProject) {
        set((state) => {
          if (state.projects.some((p) => p.id === id)) return state
          return {
            projects: [removedProject, ...state.projects],
            totalProjects: state.totalProjects + 1,
          }
        })
      }
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to delete project',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  batchDeleteProjects: async (ids: readonly string[]) => {
    // Deduplicate before issuing requests so a caller passing the same id
    // twice does not race two delete-API calls (one of which would return
    // 404 and trip the rollback path, restoring an actually-deleted row)
    // or inflate succeeded / failed counts in the toast.
    const uniqueIds = Array.from(new Set(ids))
    const idSet = new Set(uniqueIds)
    const previous = useProjectsStore.getState()
    const removed = previous.projects.filter((p) => idSet.has(p.id))
    set((state) => {
      const filtered = state.projects.filter((p) => !idSet.has(p.id))
      // Compute the actual removed count from the local list rather than
      // trusting the input length. If a preceding WS prune or refetch
      // already removed some IDs, the count stays consistent with the
      // list we just filtered.
      const actuallyRemoved = state.projects.length - filtered.length
      return {
        projects: filtered,
        totalProjects: Math.max(0, state.totalProjects - actuallyRemoved),
      }
    })
    const results = await Promise.allSettled(
      uniqueIds.map(async (id) => {
        await deleteProjectApi(id)
        return id
      }),
    )
    const succeededIds: string[] = []
    const failedIds: string[] = []
    const failedReasons: string[] = []
    const failedDetails: { id: string; reason: string }[] = []
    results.forEach((result, index) => {
      const id = uniqueIds[index] ?? '<unknown>'
      if (result.status === 'fulfilled') {
        succeededIds.push(result.value)
      } else {
        const reason = getErrorMessage(result.reason)
        failedIds.push(id)
        failedReasons.push(reason)
        failedDetails.push({ id, reason })
      }
    })
    if (failedIds.length > 0) {
      const failedSet = new Set(failedIds)
      const rollback = removed.filter((p) => failedSet.has(p.id))
      set((state) => {
        const existing = new Set(state.projects.map((p) => p.id))
        const toRestore = rollback.filter((p) => !existing.has(p.id))
        if (toRestore.length === 0) return state
        return {
          projects: [...toRestore, ...state.projects],
          totalProjects: state.totalProjects + toRestore.length,
        }
      })
      log.error(
        'Batch delete projects partial failure',
        sanitizeForLog({ failedCount: failedIds.length, failedDetails }),
      )
    }
    // Store owns the mutation UX: emit aggregated toasts for the batch so
    // callers do not need to assemble their own outcome messaging.
    // ``formatBatchErrors`` collapses identical reasons across the batch.
    const groupedDescription =
      failedReasons.length > 0 ? formatBatchErrors(failedReasons) : undefined
    if (succeededIds.length > 0 && failedIds.length === 0) {
      useToastStore.getState().add({
        variant: 'success',
        title:
          succeededIds.length === 1
            ? 'Project deleted'
            : `${succeededIds.length} projects deleted`,
      })
    } else if (succeededIds.length > 0 && failedIds.length > 0) {
      useToastStore.getState().add({
        variant: 'warning',
        title: `Deleted ${succeededIds.length} of ${uniqueIds.length} projects`,
        description: groupedDescription,
      })
    } else if (failedIds.length > 0) {
      useToastStore.getState().add({
        variant: 'error',
        title:
          failedIds.length === 1
            ? 'Failed to delete project'
            : `Failed to delete ${failedIds.length} projects`,
        description: groupedDescription,
      })
    }
    // Contract: delete mutations return `false` on total failure so
    // callers can rely on a single sentinel check rather than parsing
    // the counts object. Partial success still returns the counts.
    if (succeededIds.length === 0 && failedIds.length > 0) {
      return false
    }
    return {
      succeeded: succeededIds.length,
      failed: failedIds.length,
      failedReasons,
    }
  },

  setSearchQuery: (q) => set({ searchQuery: q }),
  setStatusFilter: (s) => set({ statusFilter: s }),
  setLeadFilter: (l) => set({ leadFilter: l }),

  clearDetail: () => {
    ++_detailRequestToken
    set({
      selectedProject: null,
      projectTasks: [],
      detailLoading: false,
      detailError: null,
    })
  },

  // PROJECT_DELETED: drop the row locally before the full refetch lands so
  // the UI reflects the delete immediately. Other event types fall through
  // to a full refetch -- incremental updates are not worth the complexity
  // given 30s polling.
  updateFromWsEvent: (event: WsEvent) => {
    if (event.event_type === 'project.deleted') {
      const payload = event.payload as { project_id?: unknown }
      // WS payloads are untrusted -- route the identifier through the
      // shared sanitizer so control characters / bidi / oversized
      // strings never land in local state or the UI.
      const deletedId = sanitizeWsString(payload.project_id) ?? null
      if (deletedId) {
        set((state) => {
          const filtered = state.projects.filter((p) => p.id !== deletedId)
          // If the deleted project is currently open in the detail view,
          // clear it so the user does not keep looking at a row that no
          // longer exists. The route-level guard in ProjectDetailPage will
          // redirect to the list on null selectedProject.
          const clearDetail = state.selectedProject?.id === deletedId
          return {
            projects: filtered,
            totalProjects: filtered.length !== state.projects.length
              ? Math.max(0, state.totalProjects - 1)
              : state.totalProjects,
            selectedProject: clearDetail ? null : state.selectedProject,
            projectTasks: clearDetail ? [] : state.projectTasks,
          }
        })
      }
      // Deletion is fully handled incrementally above; a follow-up
      // fetchProjects() would just be a redundant round trip.
      return
    }
    // Every other event type (creation, update, status change, ...) falls
    // through to a full refetch. Incremental updates for those are not
    // worth the complexity given the 30s poll backing store.
    void useProjectsStore.getState().fetchProjects()
  },
}))

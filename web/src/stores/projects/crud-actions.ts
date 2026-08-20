import {
  bulkDeleteProjects as bulkDeleteProjectsApi,
  createProject as createProjectApi,
  deleteProject as deleteProjectApi,
  setProjectAutonomyMode as setProjectAutonomyModeApi,
} from '@/api/endpoints/projects'
import { runBulkDelete } from '@/stores/_bulk-delete'
import { useToastStore } from '@/stores/toast'
import {
  getCrudErrorTitle,
  getErrorMessage,
} from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type { AutonomyLevel } from '@/api/types/enums'
import type {
  CreateProjectRequest,
  Project,
  ProjectAutonomyModeRequest,
} from '@/api/types/projects'
import {
  isStaleAutonomyModeRequest,
  nextAutonomyModeRequestToken,
} from './_state'
import type {
  BatchDeleteOutcome,
  ProjectsGet,
  ProjectsSet,
} from './types'

const log = createLogger('projects')

async function createProjectImpl(
  set: ProjectsSet,
  data: CreateProjectRequest,
): Promise<Project | null> {
  try {
    const project = await createProjectApi(data)
    // Optimistically add to local state for immediate UI update.
    set((state) => {
      const filtered = state.projects.filter((p) => p.id !== project.id)
      return {
        projects: [project, ...filtered],
      }
    })
    useToastStore.getState().add({
      variant: 'success',
      title: `Project ${project.name} created`,
    })
    return project
  } catch (err) {
    log.error('Create project failed:', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to create project'),
      description: getErrorMessage(err),
    })
    return null
  }
}

async function deleteProjectImpl(
  set: ProjectsSet,
  get: ProjectsGet,
  id: string,
): Promise<boolean> {
  // Capture only the specific row we optimistically remove. Restoring
  // a full snapshot on failure could resurrect projects that were
  // legitimately deleted by a concurrent request or WS update.
  const removedProject = get().projects.find((p) => p.id === id) ?? null
  set((state) => {
    const filtered = state.projects.filter((p) => p.id !== id)
    return { projects: filtered }
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
        return { projects: [removedProject, ...state.projects] }
      })
    }
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to delete project'),
      description: getErrorMessage(err),
    })
    return false
  }
}

function batchDeleteProjectsImpl(
  set: ProjectsSet,
  ids: readonly string[],
): Promise<BatchDeleteOutcome | false> {
  // Rows are dropped on the answer rather than optimistically: the backend
  // says which ones went, so there is nothing to guess and nothing to restore.
  return runBulkDelete({
    ids,
    call: bulkDeleteProjectsApi,
    removeRows: (deleted) => {
      const removed = new Set(deleted)
      set((state) => ({
        projects: state.projects.filter((project) => !removed.has(project.id)),
      }))
    },
    noun: { one: 'Project', many: 'projects' },
  })
}

async function setAutonomyModeImpl(
  set: ProjectsSet,
  get: ProjectsGet,
  id: string,
  mode: AutonomyLevel | null,
  confirm: boolean,
): Promise<Project | null> {
  // Send the displayed project version as the optimistic-concurrency guard so
  // a stale UI write surfaces a 409 instead of silently clobbering a newer
  // row. When the project is not in local state (unusual), omit the guard and
  // fall back to last-write-wins rather than blocking the write.
  const current =
    get().selectedProject?.id === id
      ? get().selectedProject
      : (get().projects.find((p) => p.id === id) ?? null)
  const request: ProjectAutonomyModeRequest =
    current !== null
      ? { mode, confirm, expected_version: current.version }
      : { mode, confirm }
  // Latest-wins guard: two quick changes race, and only the newest
  // response is allowed to write state (or clear the saving flag), so a
  // slower earlier PATCH cannot clobber a newer selection.
  const token = nextAutonomyModeRequestToken(id)
  set({ autonomyModeSaving: true })
  try {
    const project = await setProjectAutonomyModeApi(id, request)
    if (isStaleAutonomyModeRequest(id, token)) return project
    set((state) => ({
      projects: state.projects.map((p) => (p.id === project.id ? project : p)),
      selectedProject:
        state.selectedProject?.id === project.id
          ? project
          : state.selectedProject,
      autonomyModeSaving: false,
    }))
    useToastStore.getState().add({
      variant: 'success',
      title: `Oversight mode updated for ${project.name}`,
    })
    return project
  } catch (err) {
    log.error('Set project autonomy mode failed:', sanitizeForLog(err))
    if (isStaleAutonomyModeRequest(id, token)) return null
    set({ autonomyModeSaving: false })
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to update oversight mode'),
      description: getErrorMessage(err),
    })
    return null
  }
}

export function createCrudActions(set: ProjectsSet, get: ProjectsGet) {
  return {
    createProject: (data: CreateProjectRequest) =>
      createProjectImpl(set, data),
    setAutonomyMode: (id: string, mode: AutonomyLevel | null, confirm = false) =>
      setAutonomyModeImpl(set, get, id, mode, confirm),
    deleteProject: (id: string) => deleteProjectImpl(set, get, id),
    batchDeleteProjects: (ids: readonly string[]) =>
      batchDeleteProjectsImpl(set, ids),
  }
}

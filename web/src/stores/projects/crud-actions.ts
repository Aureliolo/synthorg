import {
  createProject as createProjectApi,
  deleteProject as deleteProjectApi,
} from '@/api/endpoints/projects'
import { useToastStore } from '@/stores/toast'
import {
  formatBatchErrors,
  getCrudErrorTitle,
  getErrorMessage,
} from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  CreateProjectRequest,
  Project,
} from '@/api/types/projects'
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

interface BatchSettlement {
  succeededIds: string[]
  failedIds: string[]
  failedReasons: string[]
  failedDetails: { id: string; reason: string }[]
}

function settleBatchResults(
  results: readonly PromiseSettledResult<string>[],
  uniqueIds: readonly string[],
): BatchSettlement {
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
  return { succeededIds, failedIds, failedReasons, failedDetails }
}

function applyOptimisticBatchRemoval(
  set: ProjectsSet,
  uniqueIds: readonly string[],
): readonly Project[] {
  const idSet = new Set(uniqueIds)
  let removed: readonly Project[] = []
  set((state) => {
    removed = state.projects.filter((p) => idSet.has(p.id))
    const filtered = state.projects.filter((p) => !idSet.has(p.id))
    return { projects: filtered }
  })
  return removed
}

function rollbackFailedDeletes(
  set: ProjectsSet,
  removed: readonly Project[],
  failedIds: readonly string[],
): void {
  if (failedIds.length === 0) return
  const failedSet = new Set(failedIds)
  const rollback = removed.filter((p) => failedSet.has(p.id))
  set((state) => {
    const existing = new Set(state.projects.map((p) => p.id))
    const toRestore = rollback.filter((p) => !existing.has(p.id))
    if (toRestore.length === 0) return state
    return { projects: [...toRestore, ...state.projects] }
  })
}

function buildBatchToast(
  uniqueCount: number,
  succeededCount: number,
  failedCount: number,
  description: string | undefined,
) {
  if (succeededCount > 0 && failedCount === 0) {
    return {
      variant: 'success' as const,
      title: succeededCount === 1
        ? 'Project deleted'
        : `${succeededCount} projects deleted`,
    }
  }
  if (succeededCount > 0 && failedCount > 0) {
    return {
      variant: 'warning' as const,
      title: `Deleted ${succeededCount} of ${uniqueCount} projects`,
      description,
    }
  }
  return {
    variant: 'error' as const,
    title: failedCount === 1
      ? 'Failed to delete project'
      : `Failed to delete ${failedCount} projects`,
    description,
  }
}

function emitBatchToast(
  uniqueIds: readonly string[],
  settlement: BatchSettlement,
): void {
  const description = settlement.failedReasons.length > 0
    ? formatBatchErrors(settlement.failedReasons)
    : undefined
  if (
    settlement.succeededIds.length === 0
    && settlement.failedIds.length === 0
  ) return
  useToastStore.getState().add(buildBatchToast(
    uniqueIds.length,
    settlement.succeededIds.length,
    settlement.failedIds.length,
    description,
  ))
}

async function batchDeleteProjectsImpl(
  set: ProjectsSet,
  ids: readonly string[],
): Promise<BatchDeleteOutcome | false> {
  const uniqueIds = Array.from(new Set(ids))
  // Outer guard mirrors batchDeleteWorkflowsImpl: an unexpected throw from any
  // helper (optimistic removal, settlement, rollback, toast) must not escape
  // the store-mutation contract or leave the store partially rolled back with
  // no user feedback. Surface a fallback error toast and return the `false`
  // sentinel so callers (which never wrap store calls in try/catch) are safe.
  try {
    const removed = applyOptimisticBatchRemoval(set, uniqueIds)
    const results = await Promise.allSettled(
      uniqueIds.map(async (id) => {
        await deleteProjectApi(id)
        return id
      }),
    )
    const settlement = settleBatchResults(results, uniqueIds)
    rollbackFailedDeletes(set, removed, settlement.failedIds)
    if (settlement.failedDetails.length > 0) {
      log.error(
        'Batch delete projects partial failure',
        sanitizeForLog({
          failedCount: settlement.failedIds.length,
          failedDetails: settlement.failedDetails,
        }),
      )
    }
    emitBatchToast(uniqueIds, settlement)
    if (settlement.succeededIds.length === 0 && settlement.failedIds.length > 0) {
      return false
    }
    return {
      succeeded: settlement.succeededIds.length,
      failed: settlement.failedIds.length,
      failedReasons: settlement.failedReasons,
    }
  } catch (err) {
    log.error('Batch delete projects failed unexpectedly', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(
        err,
        uniqueIds.length === 1
          ? 'Failed to delete project'
          : `Failed to delete ${uniqueIds.length} projects`,
      ),
      description: getErrorMessage(err),
    })
    return false
  }
}

export function createCrudActions(set: ProjectsSet, get: ProjectsGet) {
  return {
    createProject: (data: CreateProjectRequest) =>
      createProjectImpl(set, data),
    deleteProject: (id: string) => deleteProjectImpl(set, get, id),
    batchDeleteProjects: (ids: readonly string[]) =>
      batchDeleteProjectsImpl(set, ids),
  }
}

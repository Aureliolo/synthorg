import {
  createFromBlueprint as createFromBlueprintApi,
  createWorkflow as createWorkflowApi,
  deleteWorkflow as deleteWorkflowApi,
} from '@/api/endpoints/workflows'
import { useToastStore } from '@/stores/toast'
import { formatBatchErrors, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  CreateFromBlueprintRequest,
  CreateWorkflowDefinitionRequest,
  WorkflowDefinition,
} from '@/api/types/workflows'
import type {
  BatchDeleteOutcome,
  WorkflowsGet,
  WorkflowsSet,
} from './types'

const log = createLogger('workflows')

/** Upsert a workflow into the store list (prepends, deduplicates). */
function upsertWorkflow(
  set: WorkflowsSet,
  workflow: WorkflowDefinition,
): void {
  set((state) => {
    const exists = state.workflows.some((w) => w.id === workflow.id)
    const filtered = state.workflows.filter((w) => w.id !== workflow.id)
    return {
      workflows: [workflow, ...filtered],
      totalWorkflows: exists
        ? state.totalWorkflows
        : state.totalWorkflows + 1,
    }
  })
}

async function createWorkflowImpl(
  set: WorkflowsSet,
  data: CreateWorkflowDefinitionRequest,
): Promise<WorkflowDefinition | null> {
  try {
    const workflow = await createWorkflowApi(data)
    upsertWorkflow(set, workflow)
    useToastStore.getState().add({
      variant: 'success',
      title: `Workflow ${workflow.name} created`,
    })
    return workflow
  } catch (err) {
    log.error('Create workflow failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to create workflow',
      description: getErrorMessage(err),
    })
    return null
  }
}

async function createFromBlueprintImpl(
  set: WorkflowsSet,
  data: CreateFromBlueprintRequest,
): Promise<WorkflowDefinition | null> {
  try {
    const workflow = await createFromBlueprintApi(data)
    upsertWorkflow(set, workflow)
    useToastStore.getState().add({
      variant: 'success',
      title: `Workflow ${workflow.name} created from blueprint`,
    })
    return workflow
  } catch (err) {
    log.error('Create workflow from blueprint failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to create workflow from blueprint',
      description: getErrorMessage(err),
    })
    return null
  }
}

async function deleteWorkflowImpl(
  set: WorkflowsSet,
  get: WorkflowsGet,
  id: string,
): Promise<boolean> {
  const removed = get().workflows.find((w) => w.id === id)
  set((state) => {
    const filtered = state.workflows.filter((w) => w.id !== id)
    return {
      workflows: filtered,
      totalWorkflows: filtered.length < state.workflows.length
        ? Math.max(0, state.totalWorkflows - 1)
        : state.totalWorkflows,
    }
  })
  try {
    await deleteWorkflowApi(id)
    useToastStore.getState().add({
      variant: 'success',
      title: 'Workflow deleted',
    })
    return true
  } catch (err) {
    log.error('Delete workflow failed', sanitizeForLog(err))
    // Surgical rollback: re-insert just the removed workflow if it's
    // still missing. Avoids clobbering concurrent WS-triggered updates.
    if (removed) {
      set((state) => {
        if (state.workflows.some((w) => w.id === id)) return state
        return {
          workflows: [removed, ...state.workflows],
          totalWorkflows: state.totalWorkflows + 1,
        }
      })
    }
    useToastStore.getState().add({
      variant: 'error',
      title: 'Failed to delete workflow',
      description: getErrorMessage(err),
    })
    return false
  }
}

interface BatchSettlement {
  succeededIds: string[]
  failedReasons: string[]
  failedDetails: { id: string; reason: string }[]
}

function settleBatchResults(
  results: readonly PromiseSettledResult<string>[],
  uniqueIds: readonly string[],
): BatchSettlement {
  const succeededIds: string[] = []
  const failedReasons: string[] = []
  const failedDetails: { id: string; reason: string }[] = []
  results.forEach((result, index) => {
    if (result.status === 'fulfilled') {
      succeededIds.push(result.value)
    } else {
      const id = uniqueIds[index] ?? '<unknown>'
      const reason = getErrorMessage(result.reason)
      failedReasons.push(reason)
      failedDetails.push({ id, reason })
    }
  })
  return { succeededIds, failedReasons, failedDetails }
}

function pruneSucceeded(
  set: WorkflowsSet,
  succeededIds: readonly string[],
): void {
  if (succeededIds.length === 0) return
  const deletedSet = new Set(succeededIds)
  set((state) => {
    const filtered = state.workflows.filter((w) => !deletedSet.has(w.id))
    // Decrement by the actual removed count rather than trusting
    // the request-side succeededIds length -- a preceding WS prune
    // or refetch may already have removed some IDs.
    const removedCount = state.workflows.length - filtered.length
    return {
      workflows: filtered,
      totalWorkflows: Math.max(0, state.totalWorkflows - removedCount),
    }
  })
}

function buildAllSuccessToast(succeededCount: number) {
  return {
    variant: 'success' as const,
    title: succeededCount === 1
      ? 'Workflow deleted'
      : `${succeededCount} workflows deleted`,
  }
}

function buildPartialFailureToast(
  succeededCount: number,
  uniqueCount: number,
  description: string | undefined,
) {
  return {
    variant: 'warning' as const,
    title: `Deleted ${succeededCount} of ${uniqueCount} workflows`,
    description,
  }
}

function buildAllFailureToast(
  failed: number,
  description: string | undefined,
) {
  return {
    variant: 'error' as const,
    title: failed === 1
      ? 'Failed to delete workflow'
      : `Failed to delete ${failed} workflows`,
    description,
  }
}

function emitBatchToast(
  uniqueIds: readonly string[],
  succeededIds: readonly string[],
  failed: number,
  failedReasons: readonly string[],
): void {
  const description = failedReasons.length > 0
    ? formatBatchErrors(failedReasons)
    : undefined
  const succeeded = succeededIds.length
  if (succeeded === 0 && failed === 0) return
  const toast = succeeded > 0 && failed === 0
    ? buildAllSuccessToast(succeeded)
    : succeeded > 0
      ? buildPartialFailureToast(succeeded, uniqueIds.length, description)
      : buildAllFailureToast(failed, description)
  useToastStore.getState().add(toast)
}

async function batchDeleteWorkflowsImpl(
  set: WorkflowsSet,
  ids: readonly string[],
): Promise<BatchDeleteOutcome | false> {
  try {
    // Deduplicate before issuing requests so a caller passing the
    // same id twice does not race two delete-API calls.
    const uniqueIds = Array.from(new Set(ids))
    const results = await Promise.allSettled(
      uniqueIds.map(async (id) => {
        await deleteWorkflowApi(id)
        return id
      }),
    )
    const { succeededIds, failedReasons, failedDetails } = settleBatchResults(
      results,
      uniqueIds,
    )
    pruneSucceeded(set, succeededIds)
    if (failedDetails.length > 0) {
      log.error(
        'Batch delete workflows partial failure',
        sanitizeForLog({
          failedCount: failedDetails.length,
          failedDetails,
        }),
      )
    }
    const failed = uniqueIds.length - succeededIds.length
    emitBatchToast(uniqueIds, succeededIds, failed, failedReasons)
    if (succeededIds.length === 0 && failed > 0) return false
    return { succeeded: succeededIds.length, failed, failedReasons }
  } catch (err) {
    log.error(
      'Batch delete workflows failed unexpectedly',
      sanitizeForLog(err),
    )
    const distinctCount = new Set(ids).size
    useToastStore.getState().add({
      variant: 'error',
      title: distinctCount === 1
        ? 'Failed to delete workflow'
        : `Failed to delete ${distinctCount} workflows`,
      description: getErrorMessage(err),
    })
    return false
  }
}

export function createCrudActions(set: WorkflowsSet, get: WorkflowsGet) {
  return {
    createWorkflow: (data: CreateWorkflowDefinitionRequest) =>
      createWorkflowImpl(set, data),
    createFromBlueprint: (data: CreateFromBlueprintRequest) =>
      createFromBlueprintImpl(set, data),
    deleteWorkflow: (id: string) => deleteWorkflowImpl(set, get, id),
    batchDeleteWorkflows: (ids: readonly string[]) =>
      batchDeleteWorkflowsImpl(set, ids),
  }
}

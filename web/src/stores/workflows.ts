import { create } from 'zustand'
import {
  listWorkflows,
  listBlueprints,
  createWorkflow as createWorkflowApi,
  createFromBlueprint as createFromBlueprintApi,
  deleteWorkflow as deleteWorkflowApi,
} from '@/api/endpoints/workflows'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type {
  BlueprintInfo,
  CreateFromBlueprintRequest,
  CreateWorkflowDefinitionRequest,
  WorkflowDefinition,
} from '@/api/types/workflows'

const log = createLogger('workflows')

// Page size for the cursor-paginated workflows list.  Centralised
// so the initial fetch and load-more agree on the same limit (the
// cursor-pagination contract requires it on every fetch-more call).
const WORKFLOWS_PAGE_LIMIT = 200

interface WorkflowsState {
  // List
  workflows: readonly WorkflowDefinition[]
  totalWorkflows: number
  nextCursor: string | null
  hasMore: boolean
  listLoading: boolean
  listLoadingMore: boolean
  listError: string | null

  // Blueprints
  blueprints: readonly BlueprintInfo[]
  blueprintsLoading: boolean
  blueprintsError: string | null

  // Filters
  searchQuery: string
  workflowTypeFilter: string | null

  // Actions
  fetchWorkflows: () => Promise<void>
  fetchMoreWorkflows: () => Promise<void>
  loadBlueprints: () => Promise<void>
  createWorkflow: (data: CreateWorkflowDefinitionRequest) => Promise<WorkflowDefinition | null>
  createFromBlueprint: (data: CreateFromBlueprintRequest) => Promise<WorkflowDefinition | null>
  deleteWorkflow: (id: string) => Promise<boolean>
  batchDeleteWorkflows: (
    ids: readonly string[],
  ) => Promise<
    | {
        succeeded: number
        failed: number
        failedReasons: readonly string[]
      }
    | false
  >
  setSearchQuery: (q: string) => void
  setWorkflowTypeFilter: (t: string | null) => void
  updateFromWsEvent: () => void
}

let _listRequestToken = 0
function isStaleListRequest(token: number): boolean {
  return _listRequestToken !== token
}

let _blueprintRequestToken = 0
function isStaleBlueprintRequest(token: number): boolean {
  return _blueprintRequestToken !== token
}

/** Upsert a workflow into the store list (prepends, deduplicates). */
function upsertWorkflow(
  set: (fn: (state: WorkflowsState) => Partial<WorkflowsState>) => void,
  workflow: WorkflowDefinition,
): void {
  set((state) => {
    const exists = state.workflows.some((w) => w.id === workflow.id)
    const filtered = state.workflows.filter((w) => w.id !== workflow.id)
    return {
      workflows: [workflow, ...filtered],
      totalWorkflows: exists ? state.totalWorkflows : state.totalWorkflows + 1,
    }
  })
}

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

  loadBlueprints: async () => {
    const token = ++_blueprintRequestToken
    set({ blueprintsLoading: true, blueprintsError: null })
    try {
      const data = await listBlueprints()
      if (isStaleBlueprintRequest(token)) return
      set({ blueprints: data, blueprintsLoading: false })
    } catch (err) {
      if (isStaleBlueprintRequest(token)) return
      log.warn('Failed to load blueprints', sanitizeForLog(err))
      set({ blueprintsLoading: false, blueprintsError: getErrorMessage(err) })
    }
  },

  fetchWorkflows: async () => {
    const token = ++_listRequestToken
    // Snapshot the existing pagination pair BEFORE resetting it so a
    // transient refresh failure can restore them in the catch path
    // -- otherwise an already-loaded list would be unable to fetch
    // more pages until the next successful refresh.
    const previousNextCursor = get().nextCursor
    const previousHasMore = get().hasMore
    set({
      listLoading: true,
      listLoadingMore: false,
      listError: null,
      // Reset the cursor pair on a fresh fetch so a leftover cursor
      // from a stale dataset cannot trigger a fetch-more against an
      // already-replaced page.
      nextCursor: null,
      hasMore: false,
    })
    try {
      const result = await listWorkflows({ limit: WORKFLOWS_PAGE_LIMIT })
      if (isStaleListRequest(token)) return
      set({
        workflows: result.data,
        totalWorkflows: result.data.length,
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
        listLoading: false,
      })
    } catch (err) {
      if (isStaleListRequest(token)) return
      log.warn('Failed to fetch workflows', sanitizeForLog(err))
      set({
        listLoading: false,
        listError: getErrorMessage(err),
        nextCursor: previousNextCursor,
        hasMore: previousHasMore,
      })
    }
  },

  fetchMoreWorkflows: async () => {
    // Snapshot the request token so a concurrent ``fetchWorkflows``
    // (which bumps the token) supersedes this load-more rather than
    // appending stale page data onto the replacement dataset.
    const token = _listRequestToken
    const { hasMore, nextCursor, listLoading, listLoadingMore } = get()
    if (!hasMore || !nextCursor) return
    if (listLoading || listLoadingMore) return
    set({ listLoadingMore: true, listError: null })
    try {
      const result = await listWorkflows({
        cursor: nextCursor,
        limit: WORKFLOWS_PAGE_LIMIT,
      })
      if (isStaleListRequest(token)) return
      // Recompute ``totalWorkflows`` from the merged list so the count
      // does not go stale once additional pages land (the wire envelope
      // is cursor-only and no longer carries a server-side total).
      set((state) => {
        const merged = [...state.workflows, ...result.data]
        return {
          workflows: merged,
          totalWorkflows: merged.length,
          nextCursor: result.nextCursor,
          hasMore: result.hasMore,
          listLoadingMore: false,
        }
      })
    } catch (err) {
      if (isStaleListRequest(token)) return
      log.warn('Failed to fetch more workflows', sanitizeForLog(err))
      set({ listLoadingMore: false, listError: getErrorMessage(err) })
    }
  },

  createWorkflow: async (data: CreateWorkflowDefinitionRequest) => {
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
  },

  createFromBlueprint: async (data: CreateFromBlueprintRequest) => {
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
  },

  deleteWorkflow: async (id: string) => {
    const removed = useWorkflowsStore.getState().workflows.find((w) => w.id === id)
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
      // Surgical rollback: re-insert just the removed workflow if it's still
      // missing.  Avoids clobbering concurrent WS-triggered state updates.
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
  },

  batchDeleteWorkflows: async (ids: readonly string[]) => {
    try {
      const results = await Promise.allSettled(
        ids.map(async (id) => {
          await deleteWorkflowApi(id)
          return id
        }),
      )
      const succeededIds: string[] = []
      const failedReasons: string[] = []
      results.forEach((result, index) => {
        if (result.status === 'fulfilled') {
          succeededIds.push(result.value)
        } else {
          const id = ids[index] ?? '<unknown>'
          failedReasons.push(`${id}: ${getErrorMessage(result.reason)}`)
        }
      })
      if (succeededIds.length > 0) {
        const deletedSet = new Set(succeededIds)
        set((state) => {
          const filtered = state.workflows.filter((w) => !deletedSet.has(w.id))
          // Decrement by the actual removed count rather than trusting
          // the request-side succeededIds length -- a preceding WS
          // prune or refetch may already have removed some IDs.
          const removedCount = state.workflows.length - filtered.length
          return {
            workflows: filtered,
            totalWorkflows: Math.max(0, state.totalWorkflows - removedCount),
          }
        })
      }
      if (failedReasons.length > 0) {
        log.error(
          'Batch delete workflows partial failure',
          sanitizeForLog({ failedCount: failedReasons.length, failedReasons }),
        )
      }
      // Store owns the mutation UX: aggregated toast per outcome so
      // callers do not need to assemble their own summary messaging.
      const failed = ids.length - succeededIds.length
      if (succeededIds.length > 0 && failed === 0) {
        useToastStore.getState().add({
          variant: 'success',
          title:
            succeededIds.length === 1
              ? 'Workflow deleted'
              : `${succeededIds.length} workflows deleted`,
        })
      } else if (succeededIds.length > 0 && failed > 0) {
        useToastStore.getState().add({
          variant: 'warning',
          title: `Deleted ${succeededIds.length} of ${ids.length} workflows`,
          description: failedReasons.slice(0, 3).join('; ') +
            (failedReasons.length > 3 ? `; +${failedReasons.length - 3} more` : ''),
        })
      } else if (failed > 0) {
        useToastStore.getState().add({
          variant: 'error',
          title:
            failed === 1
              ? 'Failed to delete workflow'
              : `Failed to delete ${failed} workflows`,
          description: failedReasons.slice(0, 3).join('; ') +
            (failedReasons.length > 3 ? `; +${failedReasons.length - 3} more` : ''),
        })
      }
      // Total-failure case returns the false sentinel per the store
      // mutation contract (callers only need to null-check the
      // return value to decide whether to act).
      if (succeededIds.length === 0 && failed > 0) {
        return false
      }
      return {
        succeeded: succeededIds.length,
        failed,
        failedReasons,
      }
    } catch (err) {
      // Unexpected runtime path (not a per-item Promise rejection;
      // those are caught by Promise.allSettled). Preserve the store's
      // mutation contract: log, toast, and return the false sentinel.
      log.error(
        'Batch delete workflows failed unexpectedly',
        sanitizeForLog(err),
      )
      useToastStore.getState().add({
        variant: 'error',
        title:
          ids.length === 1
            ? 'Failed to delete workflow'
            : `Failed to delete ${ids.length} workflows`,
        description: getErrorMessage(err),
      })
      return false
    }
  },

  setSearchQuery: (q) => set({ searchQuery: q }),
  setWorkflowTypeFilter: (t) => set({ workflowTypeFilter: t }),

  updateFromWsEvent: () => {
    useWorkflowsStore.getState().fetchWorkflows()
  },
}))

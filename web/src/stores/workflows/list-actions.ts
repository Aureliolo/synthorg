import {
  listBlueprints,
  listWorkflows,
} from '@/api/endpoints/workflows'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import {
  WORKFLOWS_PAGE_LIMIT,
  getListRequestToken,
  isStaleBlueprintRequest,
  isStaleListRequest,
  nextBlueprintRequestToken,
  nextListRequestToken,
} from './_state'
import type { WorkflowsGet, WorkflowsSet } from './types'

const log = createLogger('workflows')

async function loadBlueprintsImpl(set: WorkflowsSet): Promise<void> {
  const token = nextBlueprintRequestToken()
  set({ blueprintsLoading: true, blueprintsError: null })
  try {
    const data = await listBlueprints()
    if (isStaleBlueprintRequest(token)) return
    set({ blueprints: data })
  } catch (err) {
    if (isStaleBlueprintRequest(token)) return
    log.warn('Failed to load blueprints', sanitizeForLog(err))
    set({ blueprintsError: getErrorMessage(err) })
  } finally {
    // Always clear ``blueprintsLoading`` for the latest request so an
    // overlapping fetch can't strand the skeleton on.
    if (!isStaleBlueprintRequest(token)) set({ blueprintsLoading: false })
  }
}

async function fetchWorkflowsImpl(
  set: WorkflowsSet,
  get: WorkflowsGet,
): Promise<void> {
  const token = nextListRequestToken()
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
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    log.warn('Failed to fetch workflows', sanitizeForLog(err))
    set({
      listError: getErrorMessage(err),
      nextCursor: previousNextCursor,
      hasMore: previousHasMore,
    })
  } finally {
    if (!isStaleListRequest(token)) set({ listLoading: false })
  }
}

function canFetchMore(get: WorkflowsGet): boolean {
  const { hasMore, nextCursor, listLoading, listLoadingMore } = get()
  return Boolean(hasMore && nextCursor && !listLoading && !listLoadingMore)
}

async function fetchMoreWorkflowsImpl(
  set: WorkflowsSet,
  get: WorkflowsGet,
): Promise<void> {
  // Snapshot the request token so a concurrent ``fetchWorkflows``
  // (which bumps the token) supersedes this load-more rather than
  // appending stale page data onto the replacement dataset.
  const token = getListRequestToken()
  if (!canFetchMore(get)) return
  const cursor = get().nextCursor as string
  set({ listLoadingMore: true, listError: null })
  try {
    const result = await listWorkflows({
      cursor,
      limit: WORKFLOWS_PAGE_LIMIT,
    })
    if (isStaleListRequest(token)) return
    set((state) => {
      const merged = [...state.workflows, ...result.data]
      return {
        workflows: merged,
        totalWorkflows: merged.length,
        nextCursor: result.nextCursor,
        hasMore: result.hasMore,
      }
    })
  } catch (err) {
    if (isStaleListRequest(token)) return
    log.warn('Failed to fetch more workflows', sanitizeForLog(err))
    set({ listError: getErrorMessage(err) })
  } finally {
    if (!isStaleListRequest(token)) set({ listLoadingMore: false })
  }
}

export function createListActions(set: WorkflowsSet, get: WorkflowsGet) {
  return {
    fetchWorkflows: () => fetchWorkflowsImpl(set, get),
    fetchMoreWorkflows: () => fetchMoreWorkflowsImpl(set, get),
    loadBlueprints: () => loadBlueprintsImpl(set),
    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setWorkflowTypeFilter: (t: string | null) =>
      set({ workflowTypeFilter: t }),
  }
}

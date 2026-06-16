import type { StoreApi } from 'zustand'
import {
  getWorkflowDiff,
  listWorkflowVersions,
  rollbackWorkflow,
} from '@/api/endpoints/workflows'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type {
  SliceCreator,
  VersionsSlice,
  WorkflowEditorState,
} from './types'
import { parseDefinition } from './yaml'

const log = createLogger('workflow-editor:versions')

const VERSIONS_PAGE_LIMIT = 50

type WfSet = StoreApi<WorkflowEditorState>['setState']
type WfGet = StoreApi<WorkflowEditorState>['getState']

async function loadVersionsImpl(set: WfSet, get: WfGet): Promise<void> {
  const defn = get().definition
  if (!defn) return
  const reqId = get()._versionsRequestId + 1
  // Clear stale cursor state so ``loadMoreVersions`` cannot resume
  // from a cursor issued for a previous workflow definition if this
  // fresh load fails or the user switches workflows mid-flight.
  set({
    versionsLoading: true,
    _versionsRequestId: reqId,
    versionsHasMore: false,
    versionsNextCursor: null,
  })
  try {
    const result = await listWorkflowVersions(defn.id, {
      limit: VERSIONS_PAGE_LIMIT,
    })
    if (get()._versionsRequestId !== reqId) return
    set({
      versions: result.data,
      versionsLoading: false,
      versionsHasMore: result.hasMore,
      versionsNextCursor: result.nextCursor,
    })
  } catch (err) {
    if (get()._versionsRequestId !== reqId) return
    log.warn('Failed to load versions', sanitizeForLog(err))
    set({
      versionsLoading: false,
      versionsHasMore: false,
      versionsNextCursor: null,
      error: getErrorMessage(err),
    })
  }
}

async function loadMoreVersionsImpl(set: WfSet, get: WfGet): Promise<void> {
  const {
    definition: defn,
    versionsLoading,
    versionsHasMore,
    versionsNextCursor,
  } = get()
  if (!defn || versionsLoading || !versionsHasMore || !versionsNextCursor) {
    return
  }
  const reqId = get()._versionsRequestId + 1
  set({ versionsLoading: true, _versionsRequestId: reqId })
  try {
    const result = await listWorkflowVersions(defn.id, {
      limit: VERSIONS_PAGE_LIMIT,
      cursor: versionsNextCursor,
    })
    if (get()._versionsRequestId !== reqId) return
    set((prev) => ({
      versions: [...prev.versions, ...result.data],
      versionsLoading: false,
      versionsHasMore: result.hasMore,
      versionsNextCursor: result.nextCursor,
    }))
  } catch (err) {
    if (get()._versionsRequestId !== reqId) return
    log.warn('Failed to load more versions', sanitizeForLog(err))
    set({ versionsLoading: false, error: getErrorMessage(err) })
  }
}

async function loadDiffImpl(
  set: WfSet,
  get: WfGet,
  fromVersion: number,
  toVersion: number,
): Promise<void> {
  const defn = get().definition
  if (!defn) return
  const reqId = get()._diffRequestId + 1
  set({ diffLoading: true, _diffRequestId: reqId })
  try {
    const diff = await getWorkflowDiff(defn.id, fromVersion, toVersion)
    if (get()._diffRequestId !== reqId) return
    set({ diffResult: diff, diffLoading: false })
  } catch (err) {
    if (get()._diffRequestId !== reqId) return
    log.warn('Failed to load diff', sanitizeForLog(err))
    set({ diffLoading: false, error: getErrorMessage(err) })
  }
}

async function rollbackImpl(
  set: WfSet,
  get: WfGet,
  targetVersion: number,
): Promise<void> {
  const defn = get().definition
  if (!defn) return
  set({ saving: true, error: null })
  try {
    const updated = await rollbackWorkflow(defn.id, {
      target_version: targetVersion,
      expected_revision: defn.revision,
    })
    const { nodes, edges, yaml } = parseDefinition(updated)
    // ``saving`` stays true until ``loadVersions`` resolves: the Restore
    // controls are disabled while ``saving``, so re-enabling them before
    // the version list refreshes would expose a stale list and a
    // double-rollback window against the now-superseded revision.
    set((prev) => ({
      definition: updated,
      nodes,
      edges,
      yamlPreview: yaml,
      dirty: false,
      diffResult: null,
      _diffRequestId: prev._diffRequestId + 1,
      selectedNodeId: null,
      undoStack: [],
      redoStack: [],
      validationResult: null,
    }))
    await get().loadVersions()
    set({ saving: false })
    useToastStore.getState().add({
      variant: 'success',
      title: `Workflow restored to version ${targetVersion}`,
    })
  } catch (err) {
    log.warn('Rollback failed', sanitizeForLog(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to roll back workflow'),
      description: getErrorMessage(err),
    })
    set({ saving: false, error: getErrorMessage(err) })
  }
}

export const createVersionsSlice: SliceCreator<VersionsSlice> = (set, get) => ({
  versionHistoryOpen: false,
  versions: [],
  versionsLoading: false,
  versionsHasMore: false,
  versionsNextCursor: null,
  diffResult: null,
  diffLoading: false,
  _versionsRequestId: 0,
  _diffRequestId: 0,

  toggleVersionHistory: () => {
    const open = !get().versionHistoryOpen
    set({ versionHistoryOpen: open })
    if (open) void get().loadVersions()
  },

  loadVersions: () => loadVersionsImpl(set, get),
  loadMoreVersions: () => loadMoreVersionsImpl(set, get),
  loadDiff: (fromVersion, toVersion) =>
    loadDiffImpl(set, get, fromVersion, toVersion),
  clearDiff: () => {
    set((prev) => ({
      diffResult: null,
      diffLoading: false,
      _diffRequestId: prev._diffRequestId + 1,
    }))
  },
  rollback: (targetVersion) => rollbackImpl(set, get, targetVersion),
})

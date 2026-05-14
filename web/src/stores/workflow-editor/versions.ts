import {
  getWorkflowDiff,
  listWorkflowVersions,
  rollbackWorkflow,
} from '@/api/endpoints/workflows'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import type { SliceCreator, VersionsSlice } from './types'
import { parseDefinition } from './yaml'

const log = createLogger('workflow-editor:versions')

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
    if (open) {
      void get().loadVersions()
    }
  },

  loadVersions: async () => {
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
      const limit = 50
      const result = await listWorkflowVersions(defn.id, { limit })
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
  },

  loadMoreVersions: async () => {
    const {
      definition: defn,
      versionsLoading,
      versionsHasMore,
      versionsNextCursor,
    } = get()
    if (!defn || versionsLoading || !versionsHasMore || !versionsNextCursor) return
    const reqId = get()._versionsRequestId + 1
    set({ versionsLoading: true, _versionsRequestId: reqId })
    try {
      const limit = 50
      const result = await listWorkflowVersions(defn.id, {
        limit,
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
  },

  loadDiff: async (fromVersion, toVersion) => {
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
  },

  clearDiff: () => {
    set((prev) => ({
      diffResult: null,
      diffLoading: false,
      _diffRequestId: prev._diffRequestId + 1,
    }))
  },

  rollback: async (targetVersion) => {
    const defn = get().definition
    if (!defn) return
    set({ saving: true, error: null })
    try {
      const updated = await rollbackWorkflow(defn.id, {
        target_version: targetVersion,
        expected_revision: defn.revision,
      })
      const { nodes, edges, yaml } = parseDefinition(updated)
      set((prev) => ({
        definition: updated,
        nodes,
        edges,
        yamlPreview: yaml,
        saving: false,
        dirty: false,
        diffResult: null,
        _diffRequestId: prev._diffRequestId + 1,
        selectedNodeId: null,
        undoStack: [],
        redoStack: [],
        validationResult: null,
      }))
      await get().loadVersions()
    } catch (err) {
      log.warn('Rollback failed', sanitizeForLog(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Failed to roll back workflow'),
        description: getErrorMessage(err),
      })
      set({ saving: false, error: getErrorMessage(err) })
    }
  },
})

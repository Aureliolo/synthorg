import { create } from 'zustand'
import { listArtifacts, getArtifact, getArtifactContentText, deleteArtifact as deleteArtifactApi, createArtifact as createArtifactApi } from '@/api/endpoints/artifacts'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import type { Artifact, CreateArtifactRequest } from '@/api/types/artifacts'
import type { ArtifactType } from '@/api/types/enums'
import type { WsEvent } from '@/api/types/websocket'

const log = createLogger('artifacts')

/** Content types eligible for inline text preview: text/*, application/json, and YAML. */
function isPreviewableText(contentType: string): boolean {
  return (
    contentType.startsWith('text/') ||
    contentType === 'application/json' ||
    contentType === 'application/yaml' ||
    contentType === 'application/x-yaml'
  )
}

interface ArtifactsState {
  // List page
  artifacts: readonly Artifact[]
  totalArtifacts: number
  listLoading: boolean
  listError: string | null

  // Filters
  searchQuery: string
  typeFilter: ArtifactType | null
  createdByFilter: string | null
  taskIdFilter: string | null
  contentTypeFilter: string | null
  projectIdFilter: string | null

  // Detail page
  selectedArtifact: Artifact | null
  contentPreview: string | null
  detailLoading: boolean
  detailError: string | null

  // Actions. Mutations follow the canonical store error contract:
  // log + error toast + return sentinel (`false`) on failure. Callers
  // MUST NOT wrap these in try/catch.
  fetchArtifacts: () => Promise<void>
  fetchArtifactDetail: (id: string) => Promise<void>
  createArtifact: (data: CreateArtifactRequest) => Promise<Artifact | null>
  deleteArtifact: (id: string) => Promise<boolean>
  setSearchQuery: (q: string) => void
  setTypeFilter: (t: ArtifactType | null) => void
  setCreatedByFilter: (c: string | null) => void
  setTaskIdFilter: (t: string | null) => void
  setContentTypeFilter: (ct: string | null) => void
  setProjectIdFilter: (p: string | null) => void
  clearDetail: () => void
  updateFromWsEvent: (event: WsEvent) => void
}

let _detailRequestToken = 0
/** True when a newer detail request has superseded this one. */
function isStaleDetailRequest(token: number): boolean { return _detailRequestToken !== token }

let _listRequestToken = 0
/** True when a newer list request has superseded this one. */
function isStaleListRequest(token: number): boolean { return _listRequestToken !== token }

// Id of the artifact whose detail fetch is currently in-flight.
// ``fetchArtifactDetail`` clears ``selectedArtifact`` *before* awaiting
// the API, so an ``isSelected`` check alone can't invalidate a pending
// detail load when the same artifact is deleted mid-flight. Tracking
// the pending id lets ``deleteArtifact`` bump ``_detailRequestToken``
// and keep stale responses from repopulating deleted data.
let _pendingDetailId: string | null = null

export const useArtifactsStore = create<ArtifactsState>()((set) => ({
  artifacts: [],
  totalArtifacts: 0,
  listLoading: false,
  listError: null,

  searchQuery: '',
  typeFilter: null,
  createdByFilter: null,
  taskIdFilter: null,
  contentTypeFilter: null,
  projectIdFilter: null,

  selectedArtifact: null,
  contentPreview: null,
  detailLoading: false,
  detailError: null,

  fetchArtifacts: async () => {
    const token = ++_listRequestToken
    set({ listLoading: true, listError: null })
    try {
      const result = await listArtifacts({ limit: 200 })
      if (isStaleListRequest(token)) return
      set({ artifacts: result.data, totalArtifacts: result.data.length, listLoading: false })
    } catch (err) {
      if (isStaleListRequest(token)) return
      set({ listLoading: false, listError: getErrorMessage(err) })
    }
  },

  fetchArtifactDetail: async (id: string) => {
    const token = ++_detailRequestToken
    _pendingDetailId = id
    set({ detailLoading: true, detailError: null, selectedArtifact: null, contentPreview: null })
    try {
      const artifact = await getArtifact(id)
      if (isStaleDetailRequest(token)) return

      // Show metadata immediately so the detail page renders while preview loads.
      set({ selectedArtifact: artifact, detailLoading: false })

      // Fetch content preview in the background if applicable.
      if (artifact.content_type && artifact.size_bytes != null && artifact.size_bytes > 0 && isPreviewableText(artifact.content_type)) {
        try {
          const preview = await getArtifactContentText(id)
          if (isStaleDetailRequest(token)) return
          set({ contentPreview: preview })
        } catch (err) {
          if (isStaleDetailRequest(token)) return
          // Two-clause copy: title-style preface tells the user the
          // metadata IS fine, then offers the next action. The raw
          // backend message is routed through getErrorMessage so a
          // JSON / stack trace cannot leak through.
          set({
            detailError:
              `Preview failed to load: ${getErrorMessage(err)}. The full metadata is shown above; try again or download to view offline.`,
          })
        }
      }
    } catch (err) {
      if (isStaleDetailRequest(token)) return
      set({ detailLoading: false, detailError: getErrorMessage(err), selectedArtifact: null, contentPreview: null })
    } finally {
      if (_pendingDetailId === id) _pendingDetailId = null
    }
  },

  createArtifact: async (data: CreateArtifactRequest) => {
    try {
      const created = await createArtifactApi(data)
      // Bump the list-token so any in-flight ``listArtifacts`` resolves
      // as stale and cannot overwrite this optimistic insert with an
      // older snapshot. Use a functional ``set`` so the dedup check
      // observes the latest state (e.g. when a WS event for the same id
      // already arrived during the ``await``), and only increment the
      // total when we are actually adding a new id.
      _listRequestToken++
      set((state) => {
        const exists = state.artifacts.some((a) => a.id === created.id)
        const filtered = state.artifacts.filter((a) => a.id !== created.id)
        return {
          artifacts: [created, ...filtered],
          totalArtifacts: exists
            ? state.totalArtifacts
            : state.totalArtifacts + 1,
          // Bumping the list token strands any in-flight ``fetchArtifacts``
          // -- it bails on the stale check without ever clearing
          // ``listLoading``. Reset it here so the page does not stay on
          // the skeleton when a create lands during the initial load.
          listLoading: false,
        }
      })
      useToastStore.getState().add({
        variant: 'success',
        title: 'Artifact created',
      })
      return created
    } catch (err) {
      log.error(
        'Create artifact failed:',
        sanitizeForLog({ path: data.path, error: err }),
      )
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to create artifact',
        description: getErrorMessage(err),
      })
      return null
    }
  },

  deleteArtifact: async (id: string) => {
    // Optimistic delete: snapshot the slice the mutation touches,
    // apply the delete immediately so the list updates without
    // waiting on the network, then roll back on failure. Matches
    // the canonical store-mutation pattern in
    // ``stores/connections/crud-actions.ts``.
    const snapshot = useArtifactsStore.getState()
    const previous = {
      artifacts: snapshot.artifacts,
      totalArtifacts: snapshot.totalArtifacts,
      selectedArtifact: snapshot.selectedArtifact,
      contentPreview: snapshot.contentPreview,
      detailLoading: snapshot.detailLoading,
      detailError: snapshot.detailError,
    }
    _listRequestToken++
    const invalidatesPendingDetail = _pendingDetailId === id
    if (invalidatesPendingDetail) {
      _detailRequestToken++
      _pendingDetailId = null
    }
    const isSelected = snapshot.selectedArtifact?.id === id
    if (isSelected && !invalidatesPendingDetail) _detailRequestToken++
    const invalidatesDetail = isSelected || invalidatesPendingDetail
    set({
      artifacts: snapshot.artifacts.filter((a) => a.id !== id),
      totalArtifacts: Math.max(0, snapshot.totalArtifacts - 1),
      selectedArtifact: invalidatesDetail ? null : snapshot.selectedArtifact,
      contentPreview: invalidatesDetail ? null : snapshot.contentPreview,
      detailLoading: invalidatesDetail ? false : snapshot.detailLoading,
      detailError: invalidatesDetail ? null : snapshot.detailError,
    })
    try {
      await deleteArtifactApi(id)
      useToastStore.getState().add({
        variant: 'success',
        title: 'Artifact deleted',
      })
      return true
    } catch (err) {
      // Restore the pre-delete slice so the list/detail view reflects
      // the server's truth after the failed mutation. ``detailLoading``
      // is forced to ``false`` when the delete had invalidated a
      // pending detail fetch via the token bump -- we can't un-bump
      // the tokens now, so the in-flight fetch will be ignored, and
      // leaving ``detailLoading=true`` would otherwise strand the
      // detail pane on its spinner until the user navigates away.
      set(
        invalidatesDetail
          ? { ...previous, detailLoading: false }
          : previous,
      )
      log.error(
        'Delete artifact failed:',
        sanitizeForLog({ artifactId: id, error: err }),
      )
      useToastStore.getState().add({
        variant: 'error',
        title: 'Failed to delete artifact',
        description: getErrorMessage(err),
      })
      return false
    }
  },

  setSearchQuery: (q) => set({ searchQuery: q }),
  setTypeFilter: (t) => set({ typeFilter: t }),
  setCreatedByFilter: (c) => set({ createdByFilter: c }),
  setTaskIdFilter: (t) => set({ taskIdFilter: t }),
  setContentTypeFilter: (ct) => set({ contentTypeFilter: ct }),
  setProjectIdFilter: (p) => set({ projectIdFilter: p }),

  clearDetail: () => {
    ++_detailRequestToken
    set({
      selectedArtifact: null,
      contentPreview: null,
      detailLoading: false,
      detailError: null,
    })
  },

  // Event payload ignored -- all events trigger a full refetch.
  // Incremental updates are not worth the complexity given 30s polling.
  updateFromWsEvent: () => {
    void useArtifactsStore.getState().fetchArtifacts()
  },
}))

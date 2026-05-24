import { create } from 'zustand'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { createCrudActions } from './artifacts/crud-actions'
import { createDetailActions } from './artifacts/detail-actions'
import { createListActions } from './artifacts/list-actions'
import type { ArtifactsState } from './artifacts/types'

export type { ArtifactsState } from './artifacts/types'

const log = createLogger('artifacts')

export const useArtifactsStore = create<ArtifactsState>()((set, get) => ({
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

  ...createListActions(set),
  ...createDetailActions(set),
  ...createCrudActions(set, get),

  // Event payload ignored -- all events trigger a full refetch.
  // Incremental updates are not worth the complexity given 30s polling.
  updateFromWsEvent: () => {
    get().fetchArtifacts().catch((err: unknown) => {
      log.warn('artifacts ws refetch failed', sanitizeForLog(err))
    })
  },
}))

import { create } from 'zustand'
import type { ProvidersState } from './providers/types'
import { createListActions } from './providers/list-actions'
import { createDetailActions } from './providers/detail-actions'
import { createCrudActions } from './providers/crud-actions'
import { createLocalModelActions } from './providers/local-model-actions'
import { createAuditActions } from './providers/audit-actions'

export type { ProvidersState } from './providers/types'

export const useProvidersStore = create<ProvidersState>()((set, get) => ({
  // Defaults
  providers: [],
  healthMap: {},
  listLoading: false,
  listError: null,

  searchQuery: '',
  healthFilter: null,
  sortBy: 'name',
  sortDirection: 'asc',

  selectedProvider: null,
  selectedProviderModels: [],
  selectedProviderHealth: null,
  detailLoading: false,
  detailError: null,

  presets: [],
  presetsLoading: false,
  presetsError: null,
  testConnectionResult: null,
  testingConnection: false,
  discoveringModels: false,
  mutating: false,
  pullingModel: false,
  pullProgress: null,
  deletingModel: false,

  // Audit + capability detail panes
  auditEvents: [],
  auditNextCursor: null,
  auditHasMore: false,
  auditLoading: false,
  auditLoadingMore: false,
  auditError: null,
  auditProviderName: null,
  rateLimits: null,
  rateLimitsLoading: false,
  rateLimitsError: null,
  rateLimitsProviderName: null,
  presetOverride: null,
  presetOverrideLoading: false,
  presetOverrideError: null,

  // Actions (delegated to focused modules)
  ...createListActions(set),
  ...createDetailActions(set),
  ...createCrudActions(set, get),
  ...createLocalModelActions(set, get),
  ...createAuditActions(set, get),
}))

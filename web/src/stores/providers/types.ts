import type { StoreApi } from 'zustand'
import type {
  AddModelRequest,
  CreateFromPresetRequest,
  CreateProviderRequest,
  CredentialsRotateRequest,
  DiscoverModelsResponse,
  LocalModelParams,
  PresetOverride,
  PresetOverrideUpdateRequest,
  ProviderAuditEvent,
  ProviderConfig,
  ProviderHealthStatus,
  ProviderHealthSummary,
  ProviderModelResponse,
  ProviderPreset,
  PullProgressEvent,
  RateLimitsConfig,
  RateLimitsUpdateRequest,
  SyncModelsRequest,
  SyncModelsResponse,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateProviderRequest,
} from '@/api/types/providers'
import type { ProviderWithName, ProviderSortKey } from '@/utils/providers'

export interface ProvidersState {
  // List view
  providers: readonly ProviderWithName[]
  healthMap: Record<string, ProviderHealthSummary>
  listLoading: boolean
  listError: string | null

  // Filters
  searchQuery: string
  healthFilter: ProviderHealthStatus | null
  sortBy: ProviderSortKey
  sortDirection: 'asc' | 'desc'

  // Detail view
  selectedProvider: ProviderWithName | null
  selectedProviderModels: readonly ProviderModelResponse[]
  selectedProviderHealth: ProviderHealthSummary | null
  detailLoading: boolean
  detailError: string | null

  // CRUD / mutations
  presets: readonly ProviderPreset[]
  presetsLoading: boolean
  presetsError: string | null
  testConnectionResult: TestConnectionResponse | null
  testingConnection: boolean
  discoveringModels: boolean
  mutating: boolean

  // Local model management
  pullingModel: boolean
  pullProgress: PullProgressEvent | null
  deletingModel: boolean

  // Audit log (cursor-paginated, scoped to one provider at a time)
  auditEvents: readonly ProviderAuditEvent[]
  auditNextCursor: string | null
  auditHasMore: boolean
  auditLoading: boolean
  auditLoadingMore: boolean
  auditError: string | null
  /** The provider whose audit log is currently in state (or ``null``). */
  auditProviderName: string | null

  // Rate-limit overrides (read state; mutations live in crud-actions)
  rateLimits: RateLimitsConfig | null
  rateLimitsLoading: boolean
  rateLimitsError: string | null
  /** The provider whose rate-limits are currently in state (or ``null``). */
  rateLimitsProviderName: string | null

  // Preset overrides (read state; mutations live in crud-actions)
  presetOverride: PresetOverride | null
  presetOverrideLoading: boolean
  presetOverrideError: string | null
  /** The preset whose override is currently in state (or ``null``). */
  presetOverridePresetName: string | null

  // Actions
  fetchProviders: () => Promise<void>
  fetchProviderDetail: (name: string) => Promise<void>
  fetchPresets: () => Promise<void>
  createProvider: (data: CreateProviderRequest) => Promise<ProviderConfig | null>
  createFromPreset: (data: CreateFromPresetRequest) => Promise<ProviderConfig | null>
  updateProvider: (name: string, data: UpdateProviderRequest) => Promise<ProviderConfig | null>
  deleteProvider: (name: string) => Promise<boolean>
  testConnection: (name: string, data?: TestConnectionRequest) => Promise<TestConnectionResponse | null>
  discoverModels: (name: string, presetHint?: string) => Promise<DiscoverModelsResponse | null>
  clearTestResult: () => void
  clearDetail: () => void
  setSearchQuery: (q: string) => void
  setHealthFilter: (h: ProviderHealthStatus | null) => void
  setSortBy: (key: ProviderSortKey) => void
  setSortDirection: (dir: 'asc' | 'desc') => void
  pullModel: (name: string, modelName: string) => Promise<boolean>
  cancelPull: () => void
  deleteModel: (name: string, modelId: string) => Promise<boolean>
  updateModelConfig: (name: string, modelId: string, params: LocalModelParams) => Promise<boolean>

  // Audit log read actions
  fetchAudit: (providerName: string, opts?: { limit?: number }) => Promise<void>
  fetchMoreAudit: () => Promise<void>
  clearAudit: () => void

  // Rate-limit + preset-override read actions
  fetchRateLimits: (name: string) => Promise<void>
  fetchPresetOverride: (presetName: string) => Promise<void>

  // Capability mutations (six new endpoints)
  rotateCredentials: (
    name: string,
    data: CredentialsRotateRequest,
  ) => Promise<ProviderConfig | null>
  addProviderModel: (name: string, data: AddModelRequest) => Promise<ProviderConfig | null>
  syncProviderModels: (
    name: string,
    data: SyncModelsRequest,
  ) => Promise<SyncModelsResponse | null>
  updateRateLimits: (
    name: string,
    data: RateLimitsUpdateRequest,
  ) => Promise<RateLimitsConfig | null>
  updatePresetOverride: (
    presetName: string,
    data: PresetOverrideUpdateRequest,
  ) => Promise<PresetOverride | null>
  deletePresetOverride: (presetName: string) => Promise<boolean>
}

export type ProvidersSet = StoreApi<ProvidersState>['setState']
export type ProvidersGet = StoreApi<ProvidersState>['getState']

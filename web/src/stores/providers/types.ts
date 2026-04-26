import type { StoreApi } from 'zustand'
import type {
  CreateFromPresetRequest,
  CreateProviderRequest,
  DiscoverModelsResponse,
  LocalModelParams,
  ProviderConfig,
  ProviderHealthStatus,
  ProviderHealthSummary,
  ProviderModelResponse,
  ProviderPreset,
  PullProgressEvent,
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
}

export type ProvidersSet = StoreApi<ProvidersState>['setState']
export type ProvidersGet = StoreApi<ProvidersState>['getState']

import { create } from 'zustand'
import type { ProvidersState } from './providers/types'
import { createListActions, resetProviderHydration } from './providers/list-actions'
import { createDetailActions } from './providers/detail-actions'
import { createCrudActions } from './providers/crud-actions'
import { createModelMutationActions } from './providers/model-mutations'
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
  recheckingHealth: false,
  recheckingAllHealth: false,
  discoveringModels: false,
  mutating: false,
  pullingModel: false,
  pullProgress: null,
  deletingModel: false,
  updatingModelConfig: false,
  updatingCapabilityOverrides: false,
  reenablingModelIds: new Set<string>(),

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
  presetOverridePresetName: null,

  // Actions (delegated to focused modules)
  ...createListActions(set, get),
  ...createDetailActions(set, get),
  ...createCrudActions(set, get),
  ...createModelMutationActions(set, get),
  ...createLocalModelActions(set, get),
  ...createAuditActions(set, get),
}))

// Snapshotted at creation, before any test has touched the store. Replacing
// with it restores the defaults and keeps the actions, which are closures over
// the same `set` / `get` and so survive the replace unchanged. Every collection
// field is replaced rather than mutated in place by the actions, so sharing the
// snapshot's instances across resets cannot leak either.
const INITIAL_STATE: ProvidersState = useProvidersStore.getState()

/**
 * Reset the singleton store between tests.
 *
 * Backend-sourced with no client persistence, so the only cross-test leak is
 * in-memory state in a shared Vitest worker. Without this a test that renders
 * the Providers page inherits whichever providers an earlier test in the same
 * file loaded, which is invisible when it agrees with what the test expected
 * and wrong exactly when the test is about having none. The global `afterEach`
 * in `test-setup.tsx` calls this.
 */
export function resetProvidersStore(): void {
  useProvidersStore.setState(INITIAL_STATE, true)
  // Module-level, so `setState` cannot reach it: a coalesced hydration left
  // open by one test would otherwise be joined by the next, which would then
  // see the previous test's catalogue.
  resetProviderHydration()
}

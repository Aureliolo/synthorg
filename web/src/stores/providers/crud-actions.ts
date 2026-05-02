import {
  listPresets,
  createProvider as apiCreateProvider,
  createFromPreset as apiCreateFromPreset,
  updateProvider as apiUpdateProvider,
  deleteProvider as apiDeleteProvider,
  testConnection as apiTestConnection,
  discoverModels as apiDiscoverModels,
  rotateProviderCredentials as apiRotateCredentials,
  addProviderModel as apiAddModel,
  syncProviderModels as apiSyncModels,
  updateProviderRateLimits as apiUpdateRateLimits,
  updatePresetOverride as apiUpdatePresetOverride,
  deletePresetOverride as apiDeletePresetOverride,
} from '@/api/endpoints/providers'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import type {
  AddModelRequest,
  CreateFromPresetRequest,
  CreateProviderRequest,
  CredentialsRotateRequest,
  PresetOverride,
  PresetOverrideUpdateRequest,
  ProviderConfig,
  RateLimitsConfig,
  RateLimitsUpdateRequest,
  SyncModelsRequest,
  SyncModelsResponse,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateProviderRequest,
} from '@/api/types/providers'
import { useToastStore } from '@/stores/toast'
import type { ProvidersSet, ProvidersGet } from './types'

const log = createLogger('providers')

let _mutationCount = 0

function beginMutation(set: ProvidersSet): void {
  _mutationCount++
  set({ mutating: true })
}

function endMutation(set: ProvidersSet): void {
  _mutationCount = Math.max(0, _mutationCount - 1)
  if (_mutationCount === 0) set({ mutating: false })
}

export function createCrudActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    fetchPresets: async () => {
      // Presets are static backend data -- cache for the session lifetime
      if (get().presets.length > 0) return
      set({ presetsLoading: true, presetsError: null })
      try {
        const presets = await listPresets()
        set({ presets, presetsLoading: false })
      } catch (err) {
        log.warn('Failed to fetch presets:', getErrorMessage(err))
        set({ presetsLoading: false, presetsError: getErrorMessage(err) })
      }
    },

    createProvider: async (data: CreateProviderRequest) => {
      beginMutation(set)
      try {
        const config = await apiCreateProvider(data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Provider "${data.name}" created`,
        })
        await get().fetchProviders()
        return config
      } catch (err) {
        log.error('createProvider failed:', { name: sanitizeForLog(data.name), error: getErrorMessage(err) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to create provider'),
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    createFromPreset: async (data: CreateFromPresetRequest) => {
      beginMutation(set)
      try {
        const config = await apiCreateFromPreset(data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Provider "${data.name}" created from preset`,
        })
        await get().fetchProviders()
        return config
      } catch (err) {
        log.error('createFromPreset failed:', {
          name: sanitizeForLog(data.name),
          preset: sanitizeForLog(data.preset_name),
          error: getErrorMessage(err),
        })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to create provider'),
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    updateProvider: async (name: string, data: UpdateProviderRequest) => {
      beginMutation(set)
      try {
        const config = await apiUpdateProvider(name, data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Provider "${name}" updated`,
        })
        // Refresh both list and detail if viewing this provider
        await get().fetchProviders()
        if (get().selectedProvider?.name === name) {
          await get().fetchProviderDetail(name)
        }
        return config
      } catch (err) {
        log.error('updateProvider failed:', { name: sanitizeForLog(name), error: getErrorMessage(err) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to update provider'),
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    deleteProvider: async (name: string) => {
      beginMutation(set)
      try {
        await apiDeleteProvider(name)
        // Clear detail view first if we're deleting the selected provider
        // (resets _detailRequestName guard so in-flight fetches are ignored)
        if (get().selectedProvider?.name === name) {
          get().clearDetail()
        }
        // Remove from list and health map
        set((state) => ({
          providers: state.providers.filter((p) => p.name !== name),
          healthMap: Object.fromEntries(
            Object.entries(state.healthMap).filter(([k]) => k !== name),
          ),
        }))
        useToastStore.getState().add({
          variant: 'success',
          title: `Provider "${name}" deleted`,
        })
        return true
      } catch (err) {
        log.error('deleteProvider failed:', { name: sanitizeForLog(name), error: getErrorMessage(err) })
        useToastStore.getState().add({
          variant: 'error',
          ...getCrudErrorTitle(err, 'Failed to delete provider'),
          description: getErrorMessage(err),
        })
        // Refresh to restore accurate state
        await get().fetchProviders()
        return false
      } finally {
        endMutation(set)
      }
    },

    testConnection: async (name: string, data?: TestConnectionRequest) => {
      const targetProvider = name
      set({ testingConnection: true, testConnectionResult: null })
      try {
        const result = await apiTestConnection(name, data)
        // Drop stale result if user navigated away (clearDetail)
        if (get().selectedProvider?.name !== targetProvider) {
          set({ testingConnection: false })
          return null
        }
        set({ testConnectionResult: result, testingConnection: false })
        return result
      } catch (err) {
        if (get().selectedProvider?.name !== targetProvider) {
          set({ testingConnection: false })
          return null
        }
        const errorResult: TestConnectionResponse = {
          success: false,
          latency_ms: null,
          error: getErrorMessage(err),
          model_tested: null,
        }
        set({ testConnectionResult: errorResult, testingConnection: false })
        return errorResult
      }
    },

    discoverModels: async (name: string, presetHint?: string) => {
      set({ discoveringModels: true })
      try {
        const result = await apiDiscoverModels(name, presetHint)
        useToastStore.getState().add({
          variant: 'success',
          title: `Discovered ${result.discovered_models.length} models`,
        })
        // Refresh detail to show updated models
        if (get().selectedProvider?.name === name) {
          await get().fetchProviderDetail(name)
        }
        return result
      } catch (err) {
        useToastStore.getState().add({
          variant: 'error',
          title: 'Model discovery failed',
          description: getErrorMessage(err),
        })
        return null
      } finally {
        set({ discoveringModels: false })
      }
    },

    clearTestResult: () => set({ testConnectionResult: null }),

    /**
     * Rotate the credentials on an existing provider.  The variant of
     * ``data.auth_type`` must match the provider's persisted auth_type
     * or the backend rejects with 422; the toast surfaces that as a
     * normal mutation error.
     *
     * Returns the updated ``ProviderConfig`` on success or ``null``
     * on failure (toast already surfaced).
     */
    rotateCredentials: async (
      name: string,
      data: CredentialsRotateRequest,
    ): Promise<ProviderConfig | null> => {
      beginMutation(set)
      try {
        const updated = await apiRotateCredentials(name, data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Credentials rotated for "${name}"`,
        })
        await get().fetchProviders()
        return updated
      } catch (err) {
        log.warn('Failed to rotate credentials:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: `Failed to rotate credentials for "${name}"`,
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    /**
     * Add a single ``ProviderModelConfig`` to the persisted list.
     * Conflict (model id already exists) becomes a 409 + error toast.
     */
    addProviderModel: async (
      name: string,
      data: AddModelRequest,
    ): Promise<ProviderConfig | null> => {
      beginMutation(set)
      try {
        const updated = await apiAddModel(name, data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Model "${data.model.id}" added to "${name}"`,
        })
        await get().fetchProviders()
        // Refresh the active detail panel so the new model is
        // visible without a manual reload.  Mirrors the pattern in
        // ``updateProvider`` / ``discoverModels``.
        if (get().selectedProvider?.name === name) {
          await get().fetchProviderDetail(name)
        }
        return updated
      } catch (err) {
        log.warn('Failed to add model:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: `Failed to add model to "${name}"`,
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    /**
     * Re-run discovery + pricing-enrichment and merge with persisted.
     * Returns the diff (added / removed / updated id sets) plus the
     * new model list on success, ``null`` on failure.
     */
    syncProviderModels: async (
      name: string,
      data: SyncModelsRequest = {},
    ): Promise<SyncModelsResponse | null> => {
      beginMutation(set)
      try {
        const result = await apiSyncModels(name, data)
        const summary =
          result.added.length === 0 &&
          result.removed.length === 0 &&
          result.updated.length === 0
            ? 'No changes'
            : `+${result.added.length} / -${result.removed.length} / ~${result.updated.length}`
        useToastStore.getState().add({
          variant: 'success',
          title: `Models synced for "${name}"`,
          description: summary,
        })
        await get().fetchProviders()
        // Refresh the active detail panel so the synced model list
        // is visible without a manual reload.
        if (get().selectedProvider?.name === name) {
          await get().fetchProviderDetail(name)
        }
        return result
      } catch (err) {
        log.warn('Failed to sync models:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: `Failed to sync models for "${name}"`,
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    /**
     * Apply a partial update to a provider's rate-limit config.
     */
    updateRateLimits: async (
      name: string,
      data: RateLimitsUpdateRequest,
    ): Promise<RateLimitsConfig | null> => {
      beginMutation(set)
      try {
        const updated = await apiUpdateRateLimits(name, data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Rate limits updated for "${name}"`,
        })
        // Sync the rate-limits read slice the drawer is bound to;
        // otherwise the drawer keeps rendering the previous caps
        // until the user manually re-opens it.
        set((s) => {
          if (s.rateLimitsProviderName !== name) return s
          return { ...s, rateLimits: updated }
        })
        // Refresh the active detail panel so any rate-limits the
        // panel surfaces stay in sync.
        if (get().selectedProvider?.name === name) {
          await get().fetchProviderDetail(name)
        }
        return updated
      } catch (err) {
        log.warn('Failed to update rate limits:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: `Failed to update rate limits for "${name}"`,
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    /**
     * Upsert the operator override for a preset.  ``null`` fields
     * clear the override (inherit from base preset); ``undefined``
     * fields leave the override unchanged.
     */
    updatePresetOverride: async (
      presetName: string,
      data: PresetOverrideUpdateRequest,
    ): Promise<PresetOverride | null> => {
      beginMutation(set)
      try {
        const updated = await apiUpdatePresetOverride(presetName, data)
        useToastStore.getState().add({
          variant: 'success',
          title: `Preset override saved for "${presetName}"`,
        })
        // Sync the preset-override read slice the drawer is bound
        // to so the form does not keep rendering the pre-write
        // value after a successful PATCH.
        set((s) => {
          if (s.presetOverridePresetName !== presetName) return s
          return { ...s, presetOverride: updated }
        })
        return updated
      } catch (err) {
        log.warn('Failed to update preset override:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: `Failed to save preset override for "${presetName}"`,
          description: getErrorMessage(err),
        })
        return null
      } finally {
        endMutation(set)
      }
    },

    /**
     * Drop the override for ``presetName``.  Idempotent: returns
     * ``true`` whether or not a row existed; the caller distinguishes
     * via the surrounding flow if needed.
     */
    deletePresetOverride: async (presetName: string): Promise<boolean> => {
      beginMutation(set)
      try {
        await apiDeletePresetOverride(presetName)
        useToastStore.getState().add({
          variant: 'success',
          title: `Preset override cleared for "${presetName}"`,
        })
        // Drop the cached override so the drawer reverts to the
        // base preset rendering immediately.
        set((s) => {
          if (s.presetOverridePresetName !== presetName) return s
          return { ...s, presetOverride: null }
        })
        return true
      } catch (err) {
        log.warn('Failed to delete preset override:', getErrorMessage(err))
        useToastStore.getState().add({
          variant: 'error',
          title: `Failed to clear preset override for "${presetName}"`,
          description: getErrorMessage(err),
        })
        return false
      } finally {
        endMutation(set)
      }
    },
  }
}

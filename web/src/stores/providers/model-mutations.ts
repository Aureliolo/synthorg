import {
  addProviderModel as apiAddModel,
  deletePresetOverride as apiDeletePresetOverride,
  syncProviderModels as apiSyncModels,
  updateProviderRateLimits as apiUpdateRateLimits,
  updatePresetOverride as apiUpdatePresetOverride,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type {
  AddModelRequest,
  PresetOverride,
  PresetOverrideUpdateRequest,
  ProviderConfig,
  RateLimitsResponse,
  RateLimitsUpdateRequest,
  SyncModelsRequest,
  SyncModelsResponse,
} from '@/api/types/providers'
import {
  beginMutation,
  emitPlainErrorToast,
  emitSuccessToast,
  endMutation,
  refreshActiveDetail,
} from './crud-helpers'
import type { ProvidersGet, ProvidersSet } from './types'

const log = createLogger('providers')

async function refreshAfterModelMutation(
  get: ProvidersGet,
  name: string,
): Promise<void> {
  // Refresh is best-effort: a refresh failure must NOT overturn a
  // successful mutation (the server already committed the write).
  try {
    await get().fetchProviders()
    await refreshActiveDetail(get, name)
  } catch (err) {
    log.warn('Post-mutation refresh failed:', getErrorMessage(err))
    emitPlainErrorToast(
      `Provider "${name}" model list may be stale; refresh manually`,
      err,
    )
  }
}

async function addProviderModelImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  data: AddModelRequest,
): Promise<ProviderConfig | null> {
  beginMutation(set)
  let updated: ProviderConfig
  try {
    updated = await apiAddModel(name, data)
    emitSuccessToast(`Model "${data.model.id}" added to "${name}"`)
  } catch (err) {
    log.warn('Failed to add model:', getErrorMessage(err))
    emitPlainErrorToast(`Failed to add model to "${name}"`, err)
    endMutation(set)
    return null
  }
  await refreshAfterModelMutation(get, name)
  endMutation(set)
  return updated
}

function summarizeSyncResult(result: SyncModelsResponse): string {
  if (
    result.added.length === 0
    && result.removed.length === 0
    && result.updated.length === 0
  ) {
    return 'No changes'
  }
  return `+${result.added.length} / -${result.removed.length} / ~${result.updated.length}`
}

async function syncProviderModelsImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  data: SyncModelsRequest,
): Promise<SyncModelsResponse | null> {
  beginMutation(set)
  let result: SyncModelsResponse
  try {
    result = await apiSyncModels(name, data)
    emitSuccessToast(
      `Models synced for "${name}"`,
      summarizeSyncResult(result),
    )
  } catch (err) {
    log.warn('Failed to sync models:', getErrorMessage(err))
    emitPlainErrorToast(`Failed to sync models for "${name}"`, err)
    endMutation(set)
    return null
  }
  await refreshAfterModelMutation(get, name)
  endMutation(set)
  return result
}

async function updateRateLimitsImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  data: RateLimitsUpdateRequest,
): Promise<RateLimitsResponse | null> {
  beginMutation(set)
  let updated: RateLimitsResponse
  try {
    updated = await apiUpdateRateLimits(name, data)
    emitSuccessToast(`Rate limits updated for "${name}"`)
    set((s) => {
      if (s.rateLimitsProviderName !== name) return s
      return { ...s, rateLimits: updated }
    })
  } catch (err) {
    log.warn('Failed to update rate limits:', getErrorMessage(err))
    emitPlainErrorToast(`Failed to update rate limits for "${name}"`, err)
    endMutation(set)
    return null
  }
  // Refresh is best-effort; don't return null if it fails because the
  // rate-limit mutation already committed on the server.
  try {
    await refreshActiveDetail(get, name)
  } catch (err) {
    log.warn('refreshActiveDetail after rate-limit update failed:', getErrorMessage(err))
  }
  endMutation(set)
  return updated
}

async function updatePresetOverrideImpl(
  set: ProvidersSet,
  presetName: string,
  data: PresetOverrideUpdateRequest,
): Promise<PresetOverride | null> {
  beginMutation(set)
  try {
    const updated = await apiUpdatePresetOverride(presetName, data)
    emitSuccessToast(`Preset override saved for "${presetName}"`)
    set((s) => {
      if (s.presetOverridePresetName !== presetName) return s
      return { ...s, presetOverride: updated }
    })
    return updated
  } catch (err) {
    log.warn('Failed to update preset override:', getErrorMessage(err))
    emitPlainErrorToast(
      `Failed to save preset override for "${presetName}"`,
      err,
    )
    return null
  } finally {
    endMutation(set)
  }
}

async function deletePresetOverrideImpl(
  set: ProvidersSet,
  presetName: string,
): Promise<boolean> {
  beginMutation(set)
  try {
    await apiDeletePresetOverride(presetName)
    emitSuccessToast(`Preset override cleared for "${presetName}"`)
    set((s) => {
      if (s.presetOverridePresetName !== presetName) return s
      return { ...s, presetOverride: null }
    })
    return true
  } catch (err) {
    log.warn('Failed to delete preset override:', getErrorMessage(err))
    emitPlainErrorToast(
      `Failed to clear preset override for "${presetName}"`,
      err,
    )
    return false
  } finally {
    endMutation(set)
  }
}

export function createModelMutationActions(
  set: ProvidersSet,
  get: ProvidersGet,
) {
  return {
    addProviderModel: (name: string, data: AddModelRequest) =>
      addProviderModelImpl(set, get, name, data),
    syncProviderModels: (name: string, data: SyncModelsRequest) =>
      syncProviderModelsImpl(set, get, name, data),
    updateRateLimits: (name: string, data: RateLimitsUpdateRequest) =>
      updateRateLimitsImpl(set, get, name, data),
    updatePresetOverride: (
      presetName: string,
      data: PresetOverrideUpdateRequest,
    ) => updatePresetOverrideImpl(set, presetName, data),
    deletePresetOverride: (presetName: string) =>
      deletePresetOverrideImpl(set, presetName),
  }
}

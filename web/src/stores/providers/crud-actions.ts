import {
  listPresets,
  createProvider as apiCreateProvider,
  createFromPreset as apiCreateFromPreset,
  updateProvider as apiUpdateProvider,
  deleteProvider as apiDeleteProvider,
  testConnection as apiTestConnection,
  discoverModels as apiDiscoverModels,
  rotateProviderCredentials as apiRotateCredentials,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
import { useToastStore } from '@/stores/toast'
import type {
  CreateFromPresetRequest,
  CreateProviderRequest,
  CredentialsRotateRequest,
  ProviderConfig,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateProviderRequest,
} from '@/api/types/providers'
import {
  beginMutation,
  emitErrorToast,
  emitPlainErrorToast,
  emitSuccessToast,
  endMutation,
  refreshActiveDetail,
} from './crud-helpers'
import type { ProvidersSet, ProvidersGet } from './types'

const log = createLogger('providers')

async function fetchPresetsImpl(
  set: ProvidersSet,
  get: ProvidersGet,
): Promise<void> {
  if (get().presets.length > 0) return
  set({ presetsLoading: true, presetsError: null })
  try {
    const presets = await listPresets()
    set({ presets, presetsLoading: false })
  } catch (err) {
    log.warn('Failed to fetch presets:', getErrorMessage(err))
    set({ presetsLoading: false, presetsError: getErrorMessage(err) })
  }
}

async function createProviderImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  data: CreateProviderRequest,
): Promise<ProviderConfig | null> {
  beginMutation(set)
  try {
    const config = await apiCreateProvider(data)
    emitSuccessToast(`Provider "${data.name}" created`)
    await get().fetchProviders()
    return config
  } catch (err) {
    log.error('createProvider failed:', {
      name: sanitizeForLog(data.name),
      error: sanitizeForLog(getErrorMessage(err)),
    })
    emitErrorToast(err, 'Failed to create provider')
    return null
  } finally {
    endMutation(set)
  }
}

async function createFromPresetImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  data: CreateFromPresetRequest,
): Promise<ProviderConfig | null> {
  beginMutation(set)
  try {
    const config = await apiCreateFromPreset(data)
    emitSuccessToast(`Provider "${data.name}" created from preset`)
    await get().fetchProviders()
    return config
  } catch (err) {
    log.error('createFromPreset failed:', {
      name: sanitizeForLog(data.name),
      preset: sanitizeForLog(data.preset_name),
      error: sanitizeForLog(getErrorMessage(err)),
    })
    emitErrorToast(err, 'Failed to create provider')
    return null
  } finally {
    endMutation(set)
  }
}

async function updateProviderImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  data: UpdateProviderRequest,
): Promise<ProviderConfig | null> {
  beginMutation(set)
  try {
    const config = await apiUpdateProvider(name, data)
    emitSuccessToast(`Provider "${name}" updated`)
    await get().fetchProviders()
    await refreshActiveDetail(get, name)
    return config
  } catch (err) {
    log.error('updateProvider failed:', {
      name: sanitizeForLog(name),
      error: sanitizeForLog(getErrorMessage(err)),
    })
    emitErrorToast(err, 'Failed to update provider')
    return null
  } finally {
    endMutation(set)
  }
}

function removeProviderFromState(set: ProvidersSet, name: string): void {
  set((state) => ({
    providers: state.providers.filter((p) => p.name !== name),
    healthMap: Object.fromEntries(
      Object.entries(state.healthMap).filter(([k]) => k !== name),
    ),
  }))
}

async function deleteProviderImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
): Promise<boolean> {
  beginMutation(set)
  try {
    await apiDeleteProvider(name)
    if (get().selectedProvider?.name === name) get().clearDetail()
    removeProviderFromState(set, name)
    emitSuccessToast(`Provider "${name}" deleted`)
    return true
  } catch (err) {
    log.error('deleteProvider failed:', {
      name: sanitizeForLog(name),
      error: sanitizeForLog(getErrorMessage(err)),
    })
    emitErrorToast(err, 'Failed to delete provider')
    await get().fetchProviders()
    return false
  } finally {
    endMutation(set)
  }
}

async function attemptDeleteOne(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
): Promise<{ ok: true } | { ok: false; err: unknown }> {
  try {
    await apiDeleteProvider(name)
    if (get().selectedProvider?.name === name) get().clearDetail()
    removeProviderFromState(set, name)
    return { ok: true }
  } catch (err) {
    log.error('bulkDeleteProviders: delete failed', {
      name: sanitizeForLog(name),
      error: sanitizeForLog(getErrorMessage(err)),
    })
    return { ok: false, err }
  }
}

function emitBulkOutcomeToast(
  succeeded: number,
  failed: number,
  firstError: unknown,
): void {
  if (failed === 0) {
    emitSuccessToast(
      `${succeeded} provider${succeeded === 1 ? '' : 's'} deleted`,
    )
    return
  }
  if (succeeded > 0) {
    useToastStore.getState().add({
      variant: 'warning',
      title: `${succeeded} deleted; ${failed} failed`,
    })
    return
  }
  emitErrorToast(
    firstError,
    `Failed to delete ${failed} provider${failed === 1 ? '' : 's'}`,
  )
}

async function bulkDeleteProvidersImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  names: readonly string[],
): Promise<boolean> {
  if (names.length === 0) return true
  beginMutation(set)
  let succeeded = 0
  const failed: string[] = []
  let firstError: unknown = null
  try {
    for (const name of names) {
      const outcome = await attemptDeleteOne(set, get, name)
      if (outcome.ok) {
        succeeded += 1
      } else {
        firstError ??= outcome.err
        failed.push(name)
      }
    }
    emitBulkOutcomeToast(succeeded, failed.length, firstError)
    if (failed.length === 0) return true
    await get().fetchProviders()
    return false
  } finally {
    endMutation(set)
  }
}

async function testConnectionImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  data?: TestConnectionRequest,
): Promise<TestConnectionResponse | null> {
  const targetProvider = name
  set({ testingConnection: true, testConnectionResult: null })
  try {
    const result = await apiTestConnection(name, data)
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
}

async function discoverModelsImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  presetHint?: string,
) {
  set({ discoveringModels: true })
  try {
    const result = await apiDiscoverModels(name, presetHint)
    emitSuccessToast(`Discovered ${result.discovered_models.length} models`)
    await refreshActiveDetail(get, name)
    return result
  } catch (err) {
    emitPlainErrorToast('Model discovery failed', err)
    return null
  } finally {
    set({ discoveringModels: false })
  }
}

async function rotateCredentialsImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  data: CredentialsRotateRequest,
): Promise<ProviderConfig | null> {
  beginMutation(set)
  try {
    const updated = await apiRotateCredentials(name, data)
    emitSuccessToast(`Credentials rotated for "${name}"`)
    await get().fetchProviders()
    return updated
  } catch (err) {
    log.warn('Failed to rotate credentials:', getErrorMessage(err))
    emitPlainErrorToast(`Failed to rotate credentials for "${name}"`, err)
    return null
  } finally {
    endMutation(set)
  }
}

export function createCrudActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    fetchPresets: () => fetchPresetsImpl(set, get),
    createProvider: (data: CreateProviderRequest) =>
      createProviderImpl(set, get, data),
    createFromPreset: (data: CreateFromPresetRequest) =>
      createFromPresetImpl(set, get, data),
    updateProvider: (name: string, data: UpdateProviderRequest) =>
      updateProviderImpl(set, get, name, data),
    deleteProvider: (name: string) => deleteProviderImpl(set, get, name),
    bulkDeleteProviders: (names: readonly string[]) =>
      bulkDeleteProvidersImpl(set, get, names),
    testConnection: (name: string, data?: TestConnectionRequest) =>
      testConnectionImpl(set, get, name, data),
    discoverModels: (name: string, presetHint?: string) =>
      discoverModelsImpl(set, get, name, presetHint),
    clearTestResult: () => set({ testConnectionResult: null }),
    rotateCredentials: (name: string, data: CredentialsRotateRequest) =>
      rotateCredentialsImpl(set, get, name, data),
  }
}

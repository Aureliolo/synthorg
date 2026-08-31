import {
  pullModel as apiPullModel,
  deleteModel as apiDeleteModel,
  reenableToolCalling as apiReenableToolCalling,
  updateModelCapabilityOverrides as apiUpdateModelCapabilityOverrides,
  updateModelConfig as apiUpdateModelConfig,
} from '@/api/endpoints/providers'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { modelActionKey } from '@/utils/providers'
import { createLogger } from '@/lib/logger'
import type { CapabilityOverridesUpdateRequest, LocalModelParams } from '@/api/types/providers'
import { useToastStore } from '@/stores/toast'
import type { ProvidersSet, ProvidersGet } from './types'

const log = createLogger('providers')

let _pullAbortController: AbortController | null = null

async function refreshActiveDetail(
  get: ProvidersGet,
  name: string,
): Promise<void> {
  await get().fetchProviders()
  if (get().selectedProvider?.name === name) {
    await get().fetchProviderDetail(name)
  }
}

async function pullModelImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  modelName: string,
): Promise<boolean> {
  _pullAbortController?.abort()
  const controller = new AbortController()
  _pullAbortController = controller
  set({ pullingModel: true, pullProgress: null })
  let lastError: string | null = null
  try {
    await apiPullModel(
      name,
      modelName,
      (event) => {
        if (controller.signal.aborted) return
        if (event.error) lastError = event.error
        set({ pullProgress: event })
      },
      controller.signal,
    )
    // eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- assigned inside the streamed-progress onProgress callback; CFA cannot see the closure mutation
    if (lastError) {
      useToastStore.getState().add({
        variant: 'error',
        // ``lastError`` is a streamed-progress string, not an Error
        // instance, so getCrudErrorTitle can't extract a 409 / 422
        // category from it. Fall back to the literal title to keep
        // the toast meaningful.
        title: 'Model pull failed',
        description: lastError,
      })
      return false
    }
    useToastStore.getState().add({
      variant: 'success',
      title: `Model "${modelName}" pulled successfully`,
    })
    await refreshActiveDetail(get, name)
    return true
  } catch (err) {
    if ((err as Error).name !== 'AbortError') {
      log.error('Model pull failed:', getErrorMessage(err))
      useToastStore.getState().add({
        variant: 'error',
        ...getCrudErrorTitle(err, 'Model pull failed'),
        description: getErrorMessage(err),
      })
    }
    return false
  } finally {
    if (_pullAbortController === controller) {
      _pullAbortController = null
      set({ pullingModel: false })
    }
  }
}

async function deleteModelImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  modelId: string,
): Promise<boolean> {
  set({ deletingModel: true })
  try {
    await apiDeleteModel(name, modelId)
    useToastStore.getState().add({
      variant: 'success',
      title: `Model "${modelId}" deleted`,
    })
    await refreshActiveDetail(get, name)
    return true
  } catch (err) {
    log.error('Failed to delete model:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to delete model'),
      description: getErrorMessage(err),
    })
    return false
  } finally {
    set({ deletingModel: false })
  }
}

async function updateModelConfigImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  modelId: string,
  params: LocalModelParams,
): Promise<boolean> {
  set({ updatingModelConfig: true })
  try {
    await apiUpdateModelConfig(name, modelId, params)
    useToastStore.getState().add({
      variant: 'success',
      title: `Model "${modelId}" config updated`,
    })
    await refreshActiveDetail(get, name)
    return true
  } catch (err) {
    log.error('Failed to update model config:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to update model config'),
      description: getErrorMessage(err),
    })
    return false
  } finally {
    set({ updatingModelConfig: false })
  }
}

async function updateModelCapabilityOverridesImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  modelId: string,
  overrides: CapabilityOverridesUpdateRequest,
): Promise<boolean> {
  set({ updatingCapabilityOverrides: true })
  try {
    await apiUpdateModelCapabilityOverrides(name, modelId, overrides)
    useToastStore.getState().add({
      variant: 'success',
      title: `Capability overrides updated for "${modelId}"`,
    })
    await refreshActiveDetail(get, name)
    return true
  } catch (err) {
    log.error('Failed to update capability overrides:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to update capability overrides'),
      description: getErrorMessage(err),
    })
    return false
  } finally {
    set({ updatingCapabilityOverrides: false })
  }
}

async function reenableToolCallingImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
  modelId: string,
): Promise<boolean> {
  const pendingKey = modelActionKey(name, modelId)
  // Per-model concurrency: different models re-enable in parallel, but a second
  // click on a model already in flight is a no-op (its row is disabled, so this
  // only guards a double-invoke).
  if (get().reenablingModelIds.has(pendingKey)) return false
  set({ reenablingModelIds: new Set(get().reenablingModelIds).add(pendingKey) })
  try {
    await apiReenableToolCalling(name, modelId)
    // The server-side re-enable has already succeeded at this point, so a
    // follow-up refresh failure is non-fatal: log it but still resolve true
    // and show the success toast rather than reporting a failed re-enable.
    try {
      await refreshActiveDetail(get, name)
    } catch (err) {
      log.error(
        'Failed to refresh provider detail after re-enabling tool calling',
        getErrorMessage(err),
      )
    }
    useToastStore.getState().add({
      variant: 'success',
      title: `Tool calling re-enabled for "${modelId}"`,
    })
    return true
  } catch (err) {
    log.error('Failed to re-enable tool calling:', getErrorMessage(err))
    useToastStore.getState().add({
      variant: 'error',
      ...getCrudErrorTitle(err, 'Failed to re-enable tool calling'),
      description: getErrorMessage(err),
    })
    return false
  } finally {
    // Drop only this request's key so a concurrent re-enable for another model
    // keeps its own pending state.
    const next = new Set(get().reenablingModelIds)
    next.delete(pendingKey)
    set({ reenablingModelIds: next })
  }
}

export function createLocalModelActions(
  set: ProvidersSet,
  get: ProvidersGet,
) {
  return {
    pullModel: (name: string, modelName: string) =>
      pullModelImpl(set, get, name, modelName),
    cancelPull: () => {
      _pullAbortController?.abort()
      _pullAbortController = null
      set({ pullingModel: false, pullProgress: null })
    },
    deleteModel: (name: string, modelId: string) =>
      deleteModelImpl(set, get, name, modelId),
    updateModelConfig: (
      name: string,
      modelId: string,
      params: LocalModelParams,
    ) => updateModelConfigImpl(set, get, name, modelId, params),
    updateModelCapabilityOverrides: (
      name: string,
      modelId: string,
      overrides: CapabilityOverridesUpdateRequest,
    ) => updateModelCapabilityOverridesImpl(set, get, name, modelId, overrides),
    reenableToolCalling: (name: string, modelId: string) =>
      reenableToolCallingImpl(set, get, name, modelId),
  }
}

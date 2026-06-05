import {
  pullModel as apiPullModel,
  deleteModel as apiDeleteModel,
  updateModelConfig as apiUpdateModelConfig,
} from '@/api/endpoints/providers'
import { getCrudErrorTitle, getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { LocalModelParams } from '@/api/types/providers'
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
  get: ProvidersGet,
  name: string,
  modelId: string,
  params: LocalModelParams,
): Promise<boolean> {
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
    ) => updateModelConfigImpl(get, name, modelId, params),
  }
}

import {
  getProvider,
  getProviderModels,
  getProviderHealth,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { ProvidersSet } from './types'

const log = createLogger('providers')

let _detailRequestId = 0

type ProviderResult = Awaited<ReturnType<typeof getProvider>>
type ModelsResult = Awaited<ReturnType<typeof getProviderModels>>
type HealthResult = Awaited<ReturnType<typeof getProviderHealth>>

interface DetailResults {
  providerResult: PromiseSettledResult<ProviderResult>
  modelsResult: PromiseSettledResult<ModelsResult>
  healthResult: PromiseSettledResult<HealthResult>
}

async function fetchAllDetailEndpoints(
  name: string,
): Promise<DetailResults> {
  const [providerResult, modelsResult, healthResult] = await Promise.allSettled(
    [getProvider(name), getProviderModels(name), getProviderHealth(name)],
  )
  return { providerResult, modelsResult, healthResult }
}

function collectPartialErrors(results: DetailResults): string[] {
  const out: string[] = []
  if (results.modelsResult.status === 'rejected') {
    const msg = getErrorMessage(results.modelsResult.reason)
    log.warn('Failed to load models:', msg)
    out.push(`models (${msg})`)
  }
  if (results.healthResult.status === 'rejected') {
    const msg = getErrorMessage(results.healthResult.reason)
    log.warn('Failed to load health:', msg)
    out.push(`health (${msg})`)
  }
  return out
}

function setProviderNotFound(
  set: ProvidersSet,
  results: DetailResults,
): void {
  const reason = results.providerResult.status === 'rejected'
    ? results.providerResult.reason
    : null
  set({
    detailError: getErrorMessage(reason ?? 'Provider not found'),
    selectedProvider: null,
    selectedProviderModels: [],
    selectedProviderHealth: null,
    testConnectionResult: null,
  })
}

function applyDetailResults(
  set: ProvidersSet,
  results: DetailResults,
  name: string,
): void {
  const provider = results.providerResult.status === 'fulfilled'
    ? { ...results.providerResult.value, name }
    : null
  if (!provider) {
    setProviderNotFound(set, results)
    return
  }
  const partialErrors = collectPartialErrors(results)
  set({
    selectedProvider: provider,
    selectedProviderModels: results.modelsResult.status === 'fulfilled'
      ? results.modelsResult.value
      : [],
    selectedProviderHealth: results.healthResult.status === 'fulfilled'
      ? results.healthResult.value
      : null,
    detailError: partialErrors.length > 0
      ? `Some data failed to load: ${partialErrors.join(', ')}`
      : null,
  })
}

async function fetchProviderDetailImpl(
  set: ProvidersSet,
  name: string,
): Promise<void> {
  const requestId = ++_detailRequestId
  set({ detailLoading: true, detailError: null })
  const isLatest = () => requestId === _detailRequestId
  try {
    const results = await fetchAllDetailEndpoints(name)
    if (!isLatest()) return
    applyDetailResults(set, results, name)
  } catch (err) {
    if (!isLatest()) return
    log.error('Failed to fetch provider detail:', getErrorMessage(err))
    set({ detailError: getErrorMessage(err) })
  } finally {
    if (isLatest()) set({ detailLoading: false })
  }
}

export function createDetailActions(set: ProvidersSet) {
  return {
    fetchProviderDetail: (name: string) =>
      fetchProviderDetailImpl(set, name),
    clearDetail: () => {
      _detailRequestId++ // invalidate in-flight requests
      set({
        selectedProvider: null,
        selectedProviderModels: [],
        selectedProviderHealth: null,
        detailLoading: false,
        detailError: null,
        testConnectionResult: null,
        testingConnection: false,
      })
    },
  }
}

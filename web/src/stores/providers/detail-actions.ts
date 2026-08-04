import {
  getProvider,
  getProviderModels,
  getProviderHealth,
  recheckProviderHealth,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import {
  emitPlainErrorToast,
  emitSuccessToast,
  refreshActiveDetail,
} from './crud-helpers'
import { bumpHealthRevision, currentHealthRevision } from './health-revision'
import type { ProvidersGet, ProvidersSet } from './types'

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
  const reason: unknown = results.providerResult.status === 'rejected'
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
  healthRevision: number,
): void {
  const provider = results.providerResult.status === 'fulfilled'
    ? { ...results.providerResult.value, name }
    : null
  if (!provider) {
    setProviderNotFound(set, results)
    return
  }
  const partialErrors = collectPartialErrors(results)
  // Health alone is contested: a sweep that landed while this read was in
  // flight holds the newer verdict, so keep it and apply the rest.
  const healthIsCurrent = currentHealthRevision() === healthRevision
  set({
    selectedProvider: provider,
    selectedProviderModels: results.modelsResult.status === 'fulfilled'
      ? results.modelsResult.value
      : [],
    ...(healthIsCurrent
      ? {
          selectedProviderHealth: results.healthResult.status === 'fulfilled'
            ? results.healthResult.value
            : null,
        }
      : {}),
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
  // Captured before the request goes out, so any health write that lands
  // while it is in flight moves the number past this one.
  const healthRevision = currentHealthRevision()
  set({ detailLoading: true, detailError: null })
  const isLatest = () => requestId === _detailRequestId
  try {
    const results = await fetchAllDetailEndpoints(name)
    if (!isLatest()) return
    applyDetailResults(set, results, name, healthRevision)
  } catch (err) {
    if (!isLatest()) return
    log.error('Failed to fetch provider detail:', getErrorMessage(err))
    set({ detailError: getErrorMessage(err) })
  } finally {
    if (isLatest()) set({ detailLoading: false })
  }
}

async function recheckProviderHealthImpl(
  set: ProvidersSet,
  get: ProvidersGet,
  name: string,
): Promise<void> {
  // Calls the provider and lets the server record what that call found,
  // then re-reads. A plain refetch would replay the same stored aggregate,
  // which is why a fixed provider used to stay unhealthy on screen.
  set({ recheckingHealth: true })
  try {
    const summary = await recheckProviderHealth(name)
    if (get().selectedProvider?.name !== name) return
    bumpHealthRevision()
    set({ selectedProviderHealth: summary })
    // Announced, not just rendered: the badge is the only thing that moves,
    // and a colour change is invisible to a screen reader that was not
    // already on it.
    emitSuccessToast(`"${name}" is ${summary.health_status}`)
  } catch (err) {
    log.warn('Provider recheck failed:', getErrorMessage(err))
    emitPlainErrorToast('Could not recheck this provider', err)
  } finally {
    set({ recheckingHealth: false })
  }
  await refreshActiveDetail(get, name)
}

export function createDetailActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    fetchProviderDetail: (name: string) =>
      fetchProviderDetailImpl(set, name),
    recheckProviderHealth: (name: string) =>
      recheckProviderHealthImpl(set, get, name),
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

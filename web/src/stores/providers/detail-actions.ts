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

export function createDetailActions(set: ProvidersSet) {
  return {
    fetchProviderDetail: async (name: string) => {
      const requestId = ++_detailRequestId
      set({ detailLoading: true, detailError: null })
      const isLatest = () => requestId === _detailRequestId
      try {
        const [providerResult, modelsResult, healthResult] =
          await Promise.allSettled([
            getProvider(name),
            getProviderModels(name),
            getProviderHealth(name),
          ])

        if (!isLatest()) return

        const provider = providerResult.status === 'fulfilled'
          ? { ...providerResult.value, name }
          : null
        if (!provider) {
          const reason = providerResult.status === 'rejected'
            ? providerResult.reason
            : null
          set({
            detailError: getErrorMessage(reason ?? 'Provider not found'),
            selectedProvider: null,
            selectedProviderModels: [],
            selectedProviderHealth: null,
            testConnectionResult: null,
          })
          return
        }

        const partialErrors: string[] = []
        if (modelsResult.status === 'rejected') {
          const msg = getErrorMessage(modelsResult.reason)
          log.warn('Failed to load models:', msg)
          partialErrors.push(`models (${msg})`)
        }
        if (healthResult.status === 'rejected') {
          const msg = getErrorMessage(healthResult.reason)
          log.warn('Failed to load health:', msg)
          partialErrors.push(`health (${msg})`)
        }

        set({
          selectedProvider: provider,
          selectedProviderModels:
            modelsResult.status === 'fulfilled' ? modelsResult.value : [],
          selectedProviderHealth:
            healthResult.status === 'fulfilled' ? healthResult.value : null,
          detailError: partialErrors.length > 0
            ? `Some data failed to load: ${partialErrors.join(', ')}`
            : null,
        })
      } catch (err) {
        if (!isLatest()) return
        log.error('Failed to fetch provider detail:', getErrorMessage(err))
        set({ detailError: getErrorMessage(err) })
      } finally {
        // Always clear ``detailLoading`` for the LATEST request --
        // including the stale-return path -- so a race between
        // overlapping fetches can't leave the spinner stuck on. The
        // newer request will flip it back to ``true`` at its own start.
        if (isLatest()) set({ detailLoading: false })
      }
    },

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

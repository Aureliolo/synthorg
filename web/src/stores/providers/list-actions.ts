import {
  listProviders,
  getProviderHealth,
  recheckAllProviderHealth,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { normalizeProviders } from '@/utils/providers'
import { emitPlainErrorToast, emitSuccessToast } from './crud-helpers'
import { bumpHealthRevision } from './health-revision'
import type { ProviderHealthStatus, ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderSortKey } from '@/utils/providers'
import type { ProvidersGet, ProvidersSet } from './types'

const log = createLogger('providers')

let _listRequestId = 0

export function createListActions(set: ProvidersSet, get: ProvidersGet) {
  return {
    fetchProviders: async () => {
      const requestId = ++_listRequestId
      set({ listLoading: true, listError: null })
      const isLatest = () => requestId === _listRequestId
      try {
        const record = await listProviders()
        if (!isLatest()) return
        const providers = normalizeProviders(record)
        set({ providers })

        // Fetch health in parallel (best-effort, with logging)
        const names = providers.map((p) => p.name)
        const healthResults = await Promise.allSettled(
          names.map((name) => getProviderHealth(name)),
        )
        if (!isLatest()) return
        const healthMap: Record<string, ProviderHealthSummary> = {}
        for (let i = 0; i < names.length; i++) {
          const result = healthResults[i]!
          if (result.status === 'fulfilled') {
            healthMap[names[i]!] = result.value
          } else {
            const reason = getErrorMessage(result.reason)
            log.warn(
              'Failed to fetch health for provider:',
              names[i],
              reason,
            )
          }
        }
        set({ healthMap })
      } catch (err) {
        if (!isLatest()) return
        log.error('Failed to fetch providers:', getErrorMessage(err))
        set({ listError: getErrorMessage(err) })
      } finally {
        // Clear ``listLoading`` for the LATEST request even on the
        // stale-return paths so an overlapping fetch can't leave the
        // skeleton stuck on.  The newer request flips it back to true
        // at its own start, so there is no flicker.
        if (isLatest()) set({ listLoading: false })
      }
    },

    /**
     * Call every provider now and adopt the health those calls produce.
     *
     * The operator's one control when the dashboard says a provider is
     * unhealthy but they believe they have fixed it. Best-effort: the
     * server keeps each provider's recorded health when its call fails,
     * so a partial sweep still improves the picture.
     */
    recheckAllHealth: async () => {
      set({ recheckingAllHealth: true })
      try {
        const healthMap = await recheckAllProviderHealth()
        // The open detail page reads ``selectedProviderHealth``, not the
        // list's map, so a sweep that covered it has to say so there too or
        // that badge keeps the verdict the sweep just replaced.
        const selected = get().selectedProvider?.name
        const refreshed = selected === undefined ? undefined : healthMap[selected]
        // Claimed before applying, so a detail read still in flight from an
        // individual recheck drops its own health rather than resolving last
        // and restoring the verdict this sweep just replaced.
        bumpHealthRevision()
        set(
          refreshed === undefined
            ? { healthMap }
            : { healthMap, selectedProviderHealth: refreshed },
        )
        emitSuccessToast(
          `Rechecked ${Object.keys(healthMap).length} providers`,
        )
      } catch (err) {
        log.warn('Provider recheck failed:', getErrorMessage(err))
        emitPlainErrorToast('Could not recheck providers', err)
      } finally {
        set({ recheckingAllHealth: false })
      }
    },

    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setHealthFilter: (h: ProviderHealthStatus | null) => set({ healthFilter: h }),
    setSortBy: (key: ProviderSortKey) => set({ sortBy: key }),
    setSortDirection: (dir: 'asc' | 'desc') => set({ sortDirection: dir }),
  }
}

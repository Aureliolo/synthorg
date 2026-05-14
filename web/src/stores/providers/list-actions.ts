import {
  listProviders,
  getProviderHealth,
} from '@/api/endpoints/providers'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import { normalizeProviders } from '@/utils/providers'
import type { ProviderHealthStatus, ProviderHealthSummary } from '@/api/types/providers'
import type { ProviderSortKey } from '@/utils/providers'
import type { ProvidersSet } from './types'

const log = createLogger('providers')

let _listRequestId = 0

export function createListActions(set: ProvidersSet) {
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

    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setHealthFilter: (h: ProviderHealthStatus | null) => set({ healthFilter: h }),
    setSortBy: (key: ProviderSortKey) => set({ sortBy: key }),
    setSortDirection: (dir: 'asc' | 'desc') => set({ sortDirection: dir }),
  }
}

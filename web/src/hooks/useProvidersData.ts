import { useCallback, useEffect, useMemo } from 'react'
import { useProvidersStore } from '@/stores/providers'
import { usePolling } from '@/hooks/usePolling'
import { filterProviders, sortProviders } from '@/utils/providers'
import type { ProviderWithName } from '@/utils/providers'
import type { ProviderHealthSummary } from '@/api/types/providers'

const PROVIDERS_POLL_INTERVAL = 30_000

export interface UseProvidersDataReturn {
  providers: readonly ProviderWithName[]
  filteredProviders: readonly ProviderWithName[]
  healthMap: Record<string, ProviderHealthSummary>
  loading: boolean
  error: string | null
  isRefetching: boolean
}

export function useProvidersData(): UseProvidersDataReturn {
  const providers = useProvidersStore((s) => s.providers)
  const healthMap = useProvidersStore((s) => s.healthMap)
  const loading = useProvidersStore((s) => s.listLoading)
  const error = useProvidersStore((s) => s.listError)
  const searchQuery = useProvidersStore((s) => s.searchQuery)
  const healthFilter = useProvidersStore((s) => s.healthFilter)
  const sortBy = useProvidersStore((s) => s.sortBy)
  const sortDirection = useProvidersStore((s) => s.sortDirection)

  // Polling (start() fires immediately, so no separate initial fetch needed)
  const pollFn = useCallback(async () => {
    await useProvidersStore.getState().fetchProviders()
  }, [])
  const polling = usePolling(pollFn, PROVIDERS_POLL_INTERVAL)

  useEffect(() => {
    polling.start()
    return () => polling.stop()
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])

  // Client-side filtering + sorting
  const filteredProviders = useMemo(() => {
    const filtered = filterProviders(providers, healthMap, {
      search: searchQuery || undefined,
      health: healthFilter ?? undefined,
    })
    return sortProviders(filtered, healthMap, sortBy, sortDirection)
  }, [providers, healthMap, searchQuery, healthFilter, sortBy, sortDirection])

  return {
    providers,
    filteredProviders,
    healthMap,
    loading,
    error,
    isRefetching: polling.isRefetching,
  }
}

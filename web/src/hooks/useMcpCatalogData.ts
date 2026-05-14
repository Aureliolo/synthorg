import { useEffect, useMemo } from 'react'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { useMcpCatalogStore } from '@/stores/mcp-catalog'

export interface UseMcpCatalogDataReturn {
  entries: readonly McpCatalogEntry[]
  visibleEntries: readonly McpCatalogEntry[]
  loading: boolean
  searchLoading: boolean
  searchQuery: string
  hasSearch: boolean
  error: string | null
}

export function useMcpCatalogData(): UseMcpCatalogDataReturn {
  const entries = useMcpCatalogStore((s) => s.entries)
  const loading = useMcpCatalogStore((s) => s.loading)
  const error = useMcpCatalogStore((s) => s.error)
  const searchQuery = useMcpCatalogStore((s) => s.searchQuery)
  const searchResults = useMcpCatalogStore((s) => s.searchResults)
  const searchLoading = useMcpCatalogStore((s) => s.searchLoading)

  useEffect(() => {
    if (entries.length === 0 && !loading) {
      void useMcpCatalogStore.getState().fetchCatalog()
    }
    // Hydrate installed-state from the backend every mount so the
    // catalog correctly shows entries as "installed" after a refresh
    // -- the install API is write-only, so without this the local
    // ``installedEntryIds`` Set starts empty and the UI mis-renders
    // already-installed entries as fresh installs.
    void useMcpCatalogStore.getState().fetchInstalled()
    // eslint-disable-next-line @eslint-react/exhaustive-deps
  }, [])

  const visibleEntries = useMemo<readonly McpCatalogEntry[]>(() => {
    if (searchResults !== null) return searchResults
    return entries
  }, [entries, searchResults])

  return {
    entries,
    visibleEntries,
    loading,
    searchLoading,
    searchQuery,
    hasSearch: searchQuery.trim().length > 0,
    error,
  }
}

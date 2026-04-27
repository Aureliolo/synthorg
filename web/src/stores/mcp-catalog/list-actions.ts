import {
  browseMcpCatalog,
  searchMcpCatalog,
} from '@/api/endpoints/mcp-catalog'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import type { McpCatalogSet, McpCatalogState } from './types'

const log = createLogger('mcp-catalog')

let _searchDebounceHandle: ReturnType<typeof setTimeout> | null = null
let _searchGeneration = 0

export function createListActions(set: McpCatalogSet) {
  return {
    fetchCatalog: async () => {
      set({ loading: true, error: null })
      try {
        // The catalog is bundled and bounded (~20-50 entries today);
        // page through it once with a generous limit so the store keeps
        // exposing the full list to callers that don't yet paginate.
        const page = await browseMcpCatalog({ limit: 100 })
        set({ entries: page.data, loading: false })
      } catch (err) {
        log.error('Failed to fetch MCP catalog:', getErrorMessage(err))
        set({
          loading: false,
          error: getErrorMessage(err),
        })
      }
    },

    setSearchQuery: async (q: string) => {
      set({ searchQuery: q })
      if (_searchDebounceHandle !== null) {
        clearTimeout(_searchDebounceHandle)
        _searchDebounceHandle = null
      }
      if (!q.trim()) {
        set({ searchResults: null, searchLoading: false })
        return
      }
      set({ searchLoading: true })
      const generation = ++_searchGeneration
      _searchDebounceHandle = setTimeout(async () => {
        if (generation !== _searchGeneration) return
        try {
          const page = await searchMcpCatalog(q, { limit: 100 })
          if (generation !== _searchGeneration) return
          set({
            searchResults: page.data as readonly McpCatalogEntry[],
            searchLoading: false,
          })
        } catch (err) {
          if (generation !== _searchGeneration) return
          log.warn('MCP search failed:', getErrorMessage(err))
          set({ searchResults: [], searchLoading: false })
        }
      }, 200)
    },

    selectEntry: (entry: McpCatalogState['selectedEntry']) =>
      set({ selectedEntry: entry }),
  }
}

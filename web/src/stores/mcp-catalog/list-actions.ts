import {
  browseMcpCatalog,
  listInstalledMcp,
  searchMcpCatalog,
} from '@/api/endpoints/mcp-catalog'
import type { McpCatalogEntry } from '@/api/types/integrations'
import { createLogger } from '@/lib/logger'
import { getErrorMessage } from '@/utils/errors'
import {
  bumpSearchGeneration,
  cancelPendingMcpCatalogSearch,
  currentSearchGeneration,
  setSearchDebounceHandle,
} from './_state'
import type { McpCatalogSet, McpCatalogState } from './types'

const log = createLogger('mcp-catalog')

export function createListActions(set: McpCatalogSet) {
  return {
    fetchCatalog: async () => {
      set({ loading: true, error: null })
      try {
        // Walk all cursor pages so the store reflects the full bundled
        // catalog regardless of how the server chooses to chunk it. The
        // catalog is bounded (~20-50 entries today) but ``limit:100``
        // would silently truncate if it ever grew past one page.
        const all: McpCatalogEntry[] = []
        let cursor: string | null = null
        do {
          const page = await browseMcpCatalog({ limit: 100, cursor })
          all.push(...page.data)
          cursor = page.hasMore ? page.nextCursor : null
        } while (cursor !== null)
        set({ entries: all, loading: false })
      } catch (err) {
        log.error('Failed to fetch MCP catalog:', getErrorMessage(err))
        set({
          loading: false,
          error: getErrorMessage(err),
        })
      }
    },

    fetchInstalled: async () => {
      try {
        const installed = await listInstalledMcp()
        const ids = new Set(installed.map((row) => row.catalog_entry_id))
        set({ installedEntryIds: ids })
      } catch (err) {
        // Hydration is best-effort -- on failure we leave the
        // existing ``installedEntryIds`` Set in place so a transient
        // network blip doesn't blank the install badges.  The
        // catalog list error already surfaces network problems to
        // the user via the page's error banner.
        log.warn('Failed to hydrate MCP installed list:', getErrorMessage(err))
      }
    },

    setSearchQuery: (q: string) => {
      // Normalise once at the top so the same trimmed value drives the
      // stored query, the empty-check, and the upstream API call.
      // Otherwise a whitespace-padded query stores raw, short-circuits
      // on ``!q.trim()``, but the API would receive the untrimmed
      // version on the search path.
      const trimmed = q.trim()
      set({ searchQuery: trimmed })
      // Cancellation also bumps the generation, so the existing
      // generation guard below short-circuits any pending timer
      // callback that has already been dispatched by the runtime.
      cancelPendingMcpCatalogSearch()
      if (!trimmed) {
        set({ searchResults: null, searchLoading: false })
        return
      }
      set({ searchLoading: true })
      const generation = bumpSearchGeneration()
      setSearchDebounceHandle(
        setTimeout(() => {
          void (async () => {
            if (generation !== currentSearchGeneration()) return
            try {
              const page = await searchMcpCatalog(trimmed, { limit: 100 })
              if (generation !== currentSearchGeneration()) return
              set({
                searchResults: page.data,
                searchLoading: false,
              })
            } catch (err) {
              if (generation !== currentSearchGeneration()) return
              log.warn('MCP search failed:', getErrorMessage(err))
              set({ searchResults: [], searchLoading: false })
            }
          })()
        }, 200),
      )
    },

    selectEntry: (entry: McpCatalogState['selectedEntry']) =>
      set({ selectedEntry: entry }),
  }
}

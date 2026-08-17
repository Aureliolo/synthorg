import { paginateAll } from '@/api/client'
import { listAgents } from '@/api/endpoints/agents'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { AgentStatus } from '@/api/types/enums'
import type { AgentSortKey } from '@/utils/agents'
import type { AgentsSet } from './types'

const log = createLogger('agents')

/**
 * Page size for the roster walk. The full set is loaded because every
 * consumer filters, sorts or searches across the whole roster, which a
 * server cursor could only do one slice at a time.
 */
const ROSTER_PAGE_SIZE = 200

/**
 * The read currently in flight, so concurrent callers share one request.
 *
 * A `listLoading` guard alone returns immediately, which is not waiting: a
 * component that awaited `fetchAgents()` carried on against an empty roster
 * and a stale `totalAgents` while the first caller's request was still open.
 */
let inFlight: Promise<void> | null = null

async function loadAgents(set: AgentsSet): Promise<void> {
  set({ listLoading: true, listError: null })
  try {
    const agents = await paginateAll((cursor) =>
      listAgents({ limit: ROSTER_PAGE_SIZE, ...(cursor ? { cursor } : {}) }),
    )
    set({
      agents,
      totalAgents: agents.length,
      listLoading: false,
    })
  } catch (err) {
    log.warn('Failed to load agents', err)
    set({ listLoading: false, listError: getErrorMessage(err) })
  }
}

async function fetchAgentsImpl(set: AgentsSet): Promise<void> {
  // Several components can mount at once and each would otherwise fire its
  // own identical roster read. The second one awaits the first's promise
  // rather than adding a request or returning before it lands.
  inFlight ??= loadAgents(set).finally(() => {
    inFlight = null
  })
  return inFlight
}

export function createListActions(set: AgentsSet) {
  return {
    fetchAgents: () => fetchAgentsImpl(set),
    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setDepartmentFilter: (d: string | null) => set({ departmentFilter: d }),
    setStatusFilter: (s: AgentStatus | null) => set({ statusFilter: s }),
    setSortBy: (key: AgentSortKey) => set({ sortBy: key }),
    setSortDirection: (dir: 'asc' | 'desc') => set({ sortDirection: dir }),
    clearFilters: () =>
      set({
        searchQuery: '',
        departmentFilter: null,
        statusFilter: null,
      }),
  }
}

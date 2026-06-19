import { listAgents } from '@/api/endpoints/agents'
import { getErrorMessage } from '@/utils/errors'
import { createLogger } from '@/lib/logger'
import type { AgentStatus, SeniorityLevel } from '@/api/types/enums'
import type { AgentSortKey } from '@/utils/agents'
import type { AgentsSet } from './types'

const log = createLogger('agents')

async function fetchAgentsImpl(set: AgentsSet): Promise<void> {
  set({ listLoading: true, listError: null })
  try {
    const result = await listAgents({ limit: 200 })
    set({
      agents: result.data,
      totalAgents: result.data.length,
      listLoading: false,
    })
  } catch (err) {
    log.warn('Failed to load agents', err)
    set({ listLoading: false, listError: getErrorMessage(err) })
  }
}

export function createListActions(set: AgentsSet) {
  return {
    fetchAgents: () => fetchAgentsImpl(set),
    setSearchQuery: (q: string) => set({ searchQuery: q }),
    setDepartmentFilter: (d: string | null) => set({ departmentFilter: d }),
    setLevelFilter: (l: SeniorityLevel | null) => set({ levelFilter: l }),
    setStatusFilter: (s: AgentStatus | null) => set({ statusFilter: s }),
    setSortBy: (key: AgentSortKey) => set({ sortBy: key }),
    setSortDirection: (dir: 'asc' | 'desc') => set({ sortDirection: dir }),
    clearFilters: () =>
      set({
        searchQuery: '',
        departmentFilter: null,
        levelFilter: null,
        statusFilter: null,
      }),
  }
}

import type { StoreApi } from 'zustand'
// AgentConfig is the dashboard overlay (id? / status? / hiring_date?
// extras over the wire shape) so it stays imported from
// ``@/api/types/agents`` rather than the barrel; the barrel exports
// the wire-only AgentConfig which lacks those fields. Same reason for
// AgentActivityEvent: the barrel doesn't carry the alias that
// agents.ts re-exports.
import type {
  AgentActivityEvent,
  AgentConfig,
} from '@/api/types/agents'
import type {
  AgentHealthResponse,
  AgentPerformanceSummary,
  AgentStatus,
  CareerEvent,
  SeniorityLevel,
  Task,
  WsEvent,
} from '@/api/types'
import type { AgentRuntimeStatus } from '@/lib/utils'
import type { AgentSortKey } from '@/utils/agents'

export interface AgentsState {
  // List page
  agents: readonly AgentConfig[]
  totalAgents: number
  listLoading: boolean
  listError: string | null

  // Filters. ``departmentFilter`` is ``string | null`` (not
  // ``DepartmentName | null``) because departments are sourced from
  // live company config -- user-created department names are valid
  // filter values but aren't members of the static ``DepartmentName``
  // union.
  searchQuery: string
  departmentFilter: string | null
  levelFilter: SeniorityLevel | null
  statusFilter: AgentStatus | null
  sortBy: AgentSortKey
  sortDirection: 'asc' | 'desc'

  // Detail page
  selectedAgent: AgentConfig | null
  performance: AgentPerformanceSummary | null
  health: AgentHealthResponse | null
  agentTasks: readonly Task[]
  activity: readonly AgentActivityEvent[]
  activityTotal: number
  /** Opaque cursor for the next page; null on the final page. */
  activityNextCursor: string | null
  /** Whether more activity items follow the current page. */
  activityHasMore: boolean
  activityLoading: boolean
  careerHistory: readonly CareerEvent[]
  detailLoading: boolean
  detailError: string | null
  /** True while a post-setup model change is in flight. */
  updatingModel: boolean

  // Runtime statuses (org chart real-time)
  runtimeStatuses: Record<string, AgentRuntimeStatus>

  // Actions
  fetchAgents: () => Promise<void>
  fetchAgentDetail: (name: string) => Promise<void>
  fetchMoreActivity: (name: string) => Promise<void>
  setSearchQuery: (q: string) => void
  setDepartmentFilter: (d: string | null) => void
  setLevelFilter: (l: SeniorityLevel | null) => void
  setStatusFilter: (s: AgentStatus | null) => void
  setSortBy: (key: AgentSortKey) => void
  setSortDirection: (dir: 'asc' | 'desc') => void
  /** Reset every list filter (search + department + level + status) to its default. */
  clearFilters: () => void
  clearDetail: () => void
  /**
   * Re-point an agent at a new provider/model after setup. Sends the
   * update to the server, then re-fetches the agent detail to reflect
   * the canonical model state (not optimistic; the opaque ``model`` dict
   * makes a local patch unreliable). Returns false on failure (the store
   * owns the error toast).
   */
  updateAgentModel: (agentId: string, provider: string, modelId: string) => Promise<boolean>
  updateRuntimeStatus: (agentId: string, status: AgentRuntimeStatus) => void
  updateFromWsEvent: (event: WsEvent) => void
}

export type AgentsSet = StoreApi<AgentsState>['setState']
export type AgentsGet = StoreApi<AgentsState>['getState']

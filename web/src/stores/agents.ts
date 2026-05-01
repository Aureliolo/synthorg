import { create } from 'zustand'
import { listAgents, getAgent, getAgentPerformance, getAgentActivity, getAgentHistory } from '@/api/endpoints/agents'
import { listTasks } from '@/api/endpoints/tasks'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { isObject } from '@/utils/type-guards'
import { createLogger } from '@/lib/logger'
import { sanitizeWsString } from '@/stores/notifications'
import type {
  AgentActivityEvent,
  AgentConfig,
  AgentPerformanceSummary,
  CareerEvent,
} from '@/api/types/agents'
import type { AgentStatus, SeniorityLevel } from '@/api/types/enums'
import type { Task } from '@/api/types/tasks'
import type { WsEvent } from '@/api/types/websocket'
import type { AgentRuntimeStatus } from '@/lib/utils'
import type { AgentSortKey } from '@/utils/agents'

const log = createLogger('agents')

const MAX_ACTIVITIES = 100

const VALID_RUNTIME_STATUSES: ReadonlySet<string> = new Set([
  'active', 'idle', 'error', 'offline',
])

interface AgentsState {
  // List page
  agents: readonly AgentConfig[]
  totalAgents: number
  listLoading: boolean
  listError: string | null

  // Filters. ``departmentFilter`` is ``string | null`` (not
  // ``DepartmentName | null``) because departments are sourced from
  // live company config -- user-created department names are valid
  // filter values but aren't members of the static ``DepartmentName``
  // union. Consumers validate against the runtime list of configured
  // departments before applying.
  searchQuery: string
  departmentFilter: string | null
  levelFilter: SeniorityLevel | null
  statusFilter: AgentStatus | null
  sortBy: AgentSortKey
  sortDirection: 'asc' | 'desc'

  // Detail page
  selectedAgent: AgentConfig | null
  performance: AgentPerformanceSummary | null
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
  clearDetail: () => void
  updateRuntimeStatus: (agentId: string, status: AgentRuntimeStatus) => void
  updateFromWsEvent: (event: WsEvent) => void
}

// Track the latest requested agent name to prevent stale responses from overwriting
let _detailRequestName = ''

export const useAgentsStore = create<AgentsState>()((set, get) => ({
  // List page defaults
  agents: [],
  totalAgents: 0,
  listLoading: false,
  listError: null,

  // Filter defaults
  searchQuery: '',
  departmentFilter: null,
  levelFilter: null,
  statusFilter: null,
  sortBy: 'name',
  sortDirection: 'asc',

  // Detail page defaults
  selectedAgent: null,
  performance: null,
  agentTasks: [],
  activity: [],
  activityTotal: 0,
  activityNextCursor: null,
  activityHasMore: false,
  activityLoading: false,
  careerHistory: [],
  detailLoading: false,
  detailError: null,

  // Runtime statuses
  runtimeStatuses: {},

  fetchAgents: async () => {
    set({ listLoading: true, listError: null })
    try {
      const result = await listAgents({ limit: 200 })
      set({
        agents: result.data,
        totalAgents: result.total ?? result.data.length,
        listLoading: false,
      })
    } catch (err) {
      log.warn('Failed to load agents', err)
      set({ listLoading: false, listError: getErrorMessage(err) })
    }
  },

  fetchAgentDetail: async (name: string) => {
    _detailRequestName = name
    set({ detailLoading: true, detailError: null })
    try {
      const [agentResult, perfResult, tasksResult, activityResult, historyResult] =
        await Promise.allSettled([
          getAgent(name),
          getAgentPerformance(name),
          listTasks({ assigned_to: name, limit: 50 }),
          getAgentActivity(name, { limit: 20 }),
          getAgentHistory(name),
        ])

      // Guard against stale responses from rapid navigation
      if (_detailRequestName !== name) return

      const agent = agentResult.status === 'fulfilled' ? agentResult.value : null
      if (!agent) {
        const reason = agentResult.status === 'rejected' ? agentResult.reason : null
        set({ detailLoading: false, detailError: getErrorMessage(reason ?? 'Agent not found') })
        return
      }

      // Collect partial failure warnings for secondary endpoints
      const partialErrors: string[] = []
      if (perfResult.status === 'rejected') partialErrors.push('performance metrics')
      if (tasksResult.status === 'rejected') partialErrors.push('task history')
      if (activityResult.status === 'rejected') partialErrors.push('activity')
      if (historyResult.status === 'rejected') partialErrors.push('career history')

      const activityPage =
        activityResult.status === 'fulfilled' ? activityResult.value : null
      // ``total`` is nullable under cursor pagination (repo endpoints
      // omit COUNT). Fall back to the current page length so the UI
      // never displays "0" while activity items exist.
      const activityData = activityPage?.data ?? []
      set({
        selectedAgent: agent,
        performance: perfResult.status === 'fulfilled' ? perfResult.value : null,
        agentTasks: tasksResult.status === 'fulfilled' ? tasksResult.value.data : [],
        activity: activityData,
        activityTotal: activityPage?.total ?? activityData.length,
        activityNextCursor: activityPage?.nextCursor ?? null,
        activityHasMore: activityPage?.hasMore ?? false,
        careerHistory: historyResult.status === 'fulfilled' ? historyResult.value : [],
        detailLoading: false,
        detailError: partialErrors.length > 0
          ? `Some data failed to load: ${partialErrors.join(', ')}. Displayed data may be incomplete.`
          : null,
      })
    } catch (err) {
      if (_detailRequestName !== name) return
      // `name` originates from a URL segment / router param and is therefore
      // attacker-controlled; sanitize before embedding in the structured log.
      log.warn('Failed to load agent detail', { agent: sanitizeForLog(name) }, err)
      set({ detailLoading: false, detailError: getErrorMessage(err) })
    }
  },

  fetchMoreActivity: async (name: string) => {
    const {
      activity,
      selectedAgent,
      activityLoading,
      activityNextCursor,
      activityHasMore,
    } = get()
    // Short-circuit if already fetching, at client cap, no more pages,
    // or agent changed.
    if (activityLoading) return
    if (activity.length >= MAX_ACTIVITIES) return
    if (!activityHasMore || !activityNextCursor) return
    if (selectedAgent && selectedAgent.name !== name) return

    set({ activityLoading: true })
    try {
      const result = await getAgentActivity(name, {
        cursor: activityNextCursor,
        limit: 20,
      })
      // Ignore response if agent changed while fetching
      if (get().selectedAgent?.name !== name) {
        set({ activityLoading: false })
        return
      }
      set((state) => {
        const merged = [...state.activity, ...result.data].slice(0, MAX_ACTIVITIES)
        return {
          activity: merged,
          activityTotal:
            result.total === null
              ? merged.length
              : Math.min(result.total, MAX_ACTIVITIES),
          activityNextCursor: result.nextCursor,
          activityHasMore: result.hasMore,
          activityLoading: false,
        }
      })
    } catch (err) {
      // Pagination failure -- existing data preserved; surface the
      // error through ``detailError`` so the detail page can render a
      // user-visible banner instead of silently spinning forever.
      const message = getErrorMessage(err)
      set({ activityLoading: false, detailError: message })
      log.warn('Failed to load more activity', message)
    }
  },

  setSearchQuery: (q) => set({ searchQuery: q }),
  setDepartmentFilter: (d) => set({ departmentFilter: d }),
  setLevelFilter: (l) => set({ levelFilter: l }),
  setStatusFilter: (s) => set({ statusFilter: s }),
  setSortBy: (key) => set({ sortBy: key }),
  setSortDirection: (dir) => set({ sortDirection: dir }),

  clearDetail: () => {
    // Invalidate any in-flight fetchAgentDetail responses
    _detailRequestName = ''
    set({
      selectedAgent: null,
      performance: null,
      agentTasks: [],
      activity: [],
      activityTotal: 0,
      activityNextCursor: null,
      activityHasMore: false,
      activityLoading: false,
      careerHistory: [],
      detailLoading: false,
      detailError: null,
    })
  },

  updateRuntimeStatus: (agentId, status) => {
    set((state) => ({
      runtimeStatuses: { ...state.runtimeStatuses, [agentId]: status },
    }))
  },

  updateFromWsEvent: (event) => {
    // Runtime null-guard: `event.payload` is typed as `Record<string, unknown>`
    // on the wire, but a malformed broker could still send `null` or a
    // non-object.  The shared isObject narrows in one step instead of
    // a hand-rolled typeof check + a downstream `as` cast.
    if (!isObject(event.payload)) {
      log.warn('WS event dropped: payload is not an object', {
        event_type: sanitizeForLog(event.event_type),
      })
      return
    }
    if (event.event_type === 'agent.status_changed') {
      const payload = event.payload
      // Run the wire agent_id through the canonical WS sanitizer so
      // it can't carry control/bidi chars into ``runtimeStatuses`` as
      // a key -- a malformed frame would otherwise create an unusable
      // map entry that callers can't address by the real agent id.
      const rawAgentId = payload.agent_id
      const sanitizedAgentId =
        typeof rawAgentId === 'string' ? sanitizeWsString(rawAgentId) : undefined
      const status = payload.status
      if (!sanitizedAgentId || typeof status !== 'string' || !status.trim()) {
        log.warn('agent.status_changed payload missing required fields', {
          hasAgentId: typeof rawAgentId === 'string',
          hasStatus: typeof status === 'string',
        })
        return
      }
      if (sanitizedAgentId !== rawAgentId) {
        // A sanitized-mutated id means the wire value carried
        // control/bidi chars; we can't trust it to point at the
        // intended agent, so drop the event instead of aliasing to a
        // neighbouring legitimate id.
        log.warn('agent.status_changed id mutated during sanitization, skipping', {
          agent_id: sanitizeForLog(rawAgentId),
        })
        return
      }
      if (!VALID_RUNTIME_STATUSES.has(status)) {
        // `status` arrives from an untrusted WebSocket payload, so sanitize
        // before embedding in the structured log.
        log.warn('agent.status_changed received unknown status', {
          status: sanitizeForLog(status),
          knownStatuses: [...VALID_RUNTIME_STATUSES],
        })
        return
      }
      set((state) => ({
        runtimeStatuses: {
          ...state.runtimeStatuses,
          [sanitizedAgentId]: status as AgentRuntimeStatus,
        },
      }))
      return
    }
    // personality.trimmed is now handled by the unified notification
    // pipeline in useNotificationsStore.handleWsEvent (see #1078).
    if (event.event_type === 'personality.trimmed') return

    log.debug('WS event ignored: unhandled event_type', { event_type: sanitizeForLog(event?.event_type) })
  },
}))

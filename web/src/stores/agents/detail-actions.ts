import {
  getAgent,
  getAgentActivity,
  getAgentHistory,
  getAgentPerformance,
} from '@/api/endpoints/agents'
import { listTasks } from '@/api/endpoints/tasks'
import { getErrorMessage } from '@/utils/errors'
import { sanitizeForLog } from '@/utils/logging'
import { createLogger } from '@/lib/logger'
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
  AgentPerformanceSummary,
  CareerEvent,
  Task,
} from '@/api/types'
import type { PaginatedResult } from '@/api/client'
import {
  MAX_ACTIVITIES,
  clearDetailRequestId,
  getDetailRequestId,
  setDetailRequestId,
} from './_state'
import type { AgentsGet, AgentsSet } from './types'

const log = createLogger('agents')

interface DetailFetchResult {
  agentResult: PromiseSettledResult<AgentConfig>
  perfResult: PromiseSettledResult<AgentPerformanceSummary>
  tasksResult: PromiseSettledResult<{ data: Task[] }>
  activityResult: PromiseSettledResult<PaginatedResult<AgentActivityEvent>>
  historyResult: PromiseSettledResult<readonly CareerEvent[]>
}

async function fetchAllDetailEndpoints(
  agentId: string,
): Promise<DetailFetchResult> {
  const [agentResult, perfResult, tasksResult, activityResult, historyResult] =
    await Promise.allSettled([
      getAgent(agentId),
      getAgentPerformance(agentId),
      listTasks({ assigned_to: agentId, limit: 50 }),
      getAgentActivity(agentId, { limit: 20 }),
      getAgentHistory(agentId),
    ])
  return {
    agentResult,
    perfResult,
    tasksResult,
    activityResult,
    historyResult,
  }
}

function collectPartialErrors(results: DetailFetchResult): string[] {
  const out: string[] = []
  if (results.perfResult.status === 'rejected') out.push('performance metrics')
  if (results.tasksResult.status === 'rejected') out.push('task history')
  if (results.activityResult.status === 'rejected') out.push('activity')
  if (results.historyResult.status === 'rejected') out.push('career history')
  return out
}

function valueOrFallback<T>(
  result: PromiseSettledResult<T>,
  fallback: T,
): T {
  return result.status === 'fulfilled' ? result.value : fallback
}

function buildActivitySlice(
  activityResult: DetailFetchResult['activityResult'],
) {
  const page = activityResult.status === 'fulfilled'
    ? activityResult.value
    : null
  // ``total`` is nullable under cursor pagination (repo endpoints
  // omit COUNT). Fall back to the current page length so the UI
  // never displays "0" while activity items exist.
  const data = page?.data ?? []
  return {
    activity: data,
    activityTotal: data.length,
    activityNextCursor: page?.nextCursor ?? null,
    activityHasMore: page?.hasMore ?? false,
  }
}

function buildDetailErrorMessage(
  partialErrors: readonly string[],
): string | null {
  if (partialErrors.length === 0) return null
  return `Some data failed to load: ${partialErrors.join(', ')}. Displayed data may be incomplete.`
}

function buildDetailPatch(
  agent: AgentConfig,
  results: DetailFetchResult,
  partialErrors: readonly string[],
) {
  return {
    selectedAgent: agent,
    performance: valueOrFallback(results.perfResult, null),
    agentTasks: results.tasksResult.status === 'fulfilled'
      ? results.tasksResult.value.data
      : [],
    ...buildActivitySlice(results.activityResult),
    careerHistory: valueOrFallback(
      results.historyResult,
      [] as readonly CareerEvent[],
    ),
    detailLoading: false,
    detailError: buildDetailErrorMessage(partialErrors),
  }
}

async function fetchAgentDetailImpl(
  set: AgentsSet,
  agentId: string,
): Promise<void> {
  setDetailRequestId(agentId)
  set({ detailLoading: true, detailError: null })
  try {
    const results = await fetchAllDetailEndpoints(agentId)
    // Guard against stale responses from rapid navigation.
    if (getDetailRequestId() !== agentId) return
    const agent = results.agentResult.status === 'fulfilled'
      ? results.agentResult.value
      : null
    if (!agent) {
      const reason: unknown = results.agentResult.status === 'rejected'
        ? results.agentResult.reason
        : null
      // Clear every detail slice so previously-loaded data for a
      // different agent doesn't keep rendering when the new agent's
      // lookup fails.
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
        detailError: getErrorMessage(reason ?? 'Agent not found'),
      })
      return
    }
    set(buildDetailPatch(agent, results, collectPartialErrors(results)))
  } catch (err) {
    if (getDetailRequestId() !== agentId) return
    // ``agentId`` originates from a URL segment / router param and is
    // therefore attacker-controlled; sanitize before embedding in the
    // structured log.
    log.warn(
      'Failed to load agent detail',
      { agent: sanitizeForLog(agentId) },
      err,
    )
    set({ detailLoading: false, detailError: getErrorMessage(err) })
  }
}

function canFetchMoreActivity(
  get: AgentsGet,
  agentId: string,
): boolean {
  const {
    activity,
    selectedAgent,
    activityLoading,
    activityNextCursor,
    activityHasMore,
  } = get()
  if (activityLoading) return false
  if (activity.length >= MAX_ACTIVITIES) return false
  if (!activityHasMore || !activityNextCursor) return false
  if (selectedAgent && selectedAgent.id !== agentId) return false
  return true
}

async function fetchMoreActivityImpl(
  set: AgentsSet,
  get: AgentsGet,
  agentId: string,
): Promise<void> {
  if (!canFetchMoreActivity(get, agentId)) return
  const cursor = get().activityNextCursor
  if (cursor === null) return
  set({ activityLoading: true })
  try {
    const result = await getAgentActivity(agentId, { cursor, limit: 20 })
    // Ignore response if agent changed while fetching.
    if (get().selectedAgent?.id !== agentId) {
      set({ activityLoading: false })
      return
    }
    set((state) => {
      const merged = [...state.activity, ...result.data].slice(
        0,
        MAX_ACTIVITIES,
      )
      return {
        activity: merged,
        activityTotal: merged.length,
        activityNextCursor: result.nextCursor,
        activityHasMore: result.hasMore,
        activityLoading: false,
      }
    })
  } catch (err) {
    const message = getErrorMessage(err)
    set({ activityLoading: false, detailError: message })
    log.warn('Failed to load more activity', message)
  }
}

function clearDetailImpl(set: AgentsSet): void {
  clearDetailRequestId()
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
}

export function createDetailActions(set: AgentsSet, get: AgentsGet) {
  return {
    fetchAgentDetail: (agentId: string) => fetchAgentDetailImpl(set, agentId),
    fetchMoreActivity: (agentId: string) =>
      fetchMoreActivityImpl(set, get, agentId),
    clearDetail: () => clearDetailImpl(set),
  }
}

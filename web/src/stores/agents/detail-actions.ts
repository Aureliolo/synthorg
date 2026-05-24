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
import type {
  AgentActivityEvent,
  AgentConfig,
  AgentPerformanceSummary,
  CareerEvent,
} from '@/api/types/agents'
import type { Task } from '@/api/types/tasks'
import type { PaginatedResult } from '@/api/client'
import {
  MAX_ACTIVITIES,
  clearDetailRequestName,
  getDetailRequestName,
  setDetailRequestName,
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
  name: string,
): Promise<DetailFetchResult> {
  const [agentResult, perfResult, tasksResult, activityResult, historyResult] =
    await Promise.allSettled([
      getAgent(name),
      getAgentPerformance(name),
      listTasks({ assigned_to: name, limit: 50 }),
      getAgentActivity(name, { limit: 20 }),
      getAgentHistory(name),
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
  name: string,
): Promise<void> {
  setDetailRequestName(name)
  set({ detailLoading: true, detailError: null })
  try {
    const results = await fetchAllDetailEndpoints(name)
    // Guard against stale responses from rapid navigation.
    if (getDetailRequestName() !== name) return
    const agent = results.agentResult.status === 'fulfilled'
      ? results.agentResult.value
      : null
    if (!agent) {
      const reason = results.agentResult.status === 'rejected'
        ? results.agentResult.reason
        : null
      set({
        detailLoading: false,
        detailError: getErrorMessage(reason ?? 'Agent not found'),
      })
      return
    }
    set(buildDetailPatch(agent, results, collectPartialErrors(results)))
  } catch (err) {
    if (getDetailRequestName() !== name) return
    // ``name`` originates from a URL segment / router param and is
    // therefore attacker-controlled; sanitize before embedding in the
    // structured log.
    log.warn(
      'Failed to load agent detail',
      { agent: sanitizeForLog(name) },
      err,
    )
    set({ detailLoading: false, detailError: getErrorMessage(err) })
  }
}

function canFetchMoreActivity(
  get: AgentsGet,
  name: string,
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
  if (selectedAgent && selectedAgent.name !== name) return false
  return true
}

async function fetchMoreActivityImpl(
  set: AgentsSet,
  get: AgentsGet,
  name: string,
): Promise<void> {
  if (!canFetchMoreActivity(get, name)) return
  const cursor = get().activityNextCursor as string
  set({ activityLoading: true })
  try {
    const result = await getAgentActivity(name, { cursor, limit: 20 })
    // Ignore response if agent changed while fetching.
    if (get().selectedAgent?.name !== name) {
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
  clearDetailRequestName()
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
    fetchAgentDetail: (name: string) => fetchAgentDetailImpl(set, name),
    fetchMoreActivity: (name: string) =>
      fetchMoreActivityImpl(set, get, name),
    clearDetail: () => clearDetailImpl(set),
  }
}

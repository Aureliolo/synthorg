import {
  apiClient,
  paginateAll,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'

// -- Response types ----------------------------------------------------------

export interface ScalingStrategyResponse {
  name: string
  enabled: boolean
  priority: number
}

export interface ScalingSignalResponse {
  name: string
  value: number
  source: string
  threshold: number | null
  timestamp: string
}

export interface ScalingDecisionResponse {
  id: string
  action_type: string
  source_strategy: string
  target_agent_id: string | null
  target_role: string | null
  target_skills: readonly string[]
  target_department: string | null
  rationale: string
  confidence: number
  signals: readonly ScalingSignalResponse[]
  created_at: string
}

// -- API functions -----------------------------------------------------------

export async function getScalingStrategies(): Promise<ScalingStrategyResponse[]> {
  return paginateAll<ScalingStrategyResponse>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const url = qs ? `/scaling/strategies?${qs}` : '/scaling/strategies'
    const response = await apiClient.get<PaginatedResponse<ScalingStrategyResponse>>(url)
    return unwrapPaginated<ScalingStrategyResponse>(response)
  })
}

export async function getScalingDecisions(params?: {
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  cursor?: string | null
  limit?: number
}): Promise<PaginatedResult<ScalingDecisionResponse>> {
  // Return the shared ``PaginatedResult<T>`` shape so every cursor
  // endpoint in ``@/api/endpoints`` honours the same envelope, the
  // matching MSW handler can use ``paginatedFor<typeof
  // getScalingDecisions>(...)``, and stores can reuse the
  // ``nextCursor`` / ``hasMore`` / ``total`` / ``offset`` / ``limit``
  // fields without branching on a bespoke return type.
  const response = await apiClient.get<
    PaginatedResponse<ScalingDecisionResponse>
  >('/scaling/decisions', { params })
  return unwrapPaginated<ScalingDecisionResponse>(response)
}

export async function getScalingSignals(): Promise<ScalingSignalResponse[]> {
  return paginateAll<ScalingSignalResponse>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const url = qs ? `/scaling/signals?${qs}` : '/scaling/signals'
    const response = await apiClient.get<PaginatedResponse<ScalingSignalResponse>>(url)
    return unwrapPaginated<ScalingSignalResponse>(response)
  })
}

export async function triggerScalingEvaluation(): Promise<
  ScalingDecisionResponse[]
> {
  const response = await apiClient.post<ApiResponse<ScalingDecisionResponse[]>>(
    '/scaling/evaluate',
  )
  return unwrap(response)
}

export async function updateScalingStrategy(
  strategyName: string,
  enabled: boolean,
): Promise<ScalingStrategyResponse> {
  const response = await apiClient.put<ApiResponse<ScalingStrategyResponse>>(
    `/scaling/strategies/${encodeURIComponent(strategyName)}`,
    { enabled },
  )
  return unwrap(response)
}

export async function updateScalingPriority(
  order: readonly string[],
): Promise<readonly string[]> {
  const response = await apiClient.put<ApiResponse<readonly string[]>>(
    '/scaling/priority',
    { order },
  )
  return unwrap(response)
}

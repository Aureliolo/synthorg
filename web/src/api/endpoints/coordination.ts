import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { CoordinateTaskRequest, CoordinationResultResponse } from '../types/coordination'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type { CoordinationMetricsRecord } from '../types'

export async function coordinateTask(
  taskId: string,
  data?: CoordinateTaskRequest,
): Promise<CoordinationResultResponse> {
  const response = await apiClient.post<ApiResponse<CoordinationResultResponse>>(
    `/tasks/${encodeURIComponent(taskId)}/coordinate`,
    data ?? {},
  )
  return unwrap(response)
}

/** Filters accepted by ``GET /coordination/metrics`` (all AND-combined). */
export interface CoordinationMetricsFilters {
  taskId?: string
  agentId?: string
  /** ISO-8601, timezone-aware. */
  since?: string
  /** ISO-8601, timezone-aware. */
  until?: string
}

/**
 * Fetch stored coordination metrics from completed multi-agent runs,
 * newest-first. Walks every cursor page so the analytics page gets a
 * single bounded snapshot (the backend caps the query at 10,000 records)
 * and can paginate / filter client-side.
 */
export async function listCoordinationMetrics(
  filters: CoordinationMetricsFilters = {},
): Promise<readonly CoordinationMetricsRecord[]> {
  return paginateAll<CoordinationMetricsRecord>(async (cursor) => {
    const params: {
      limit: number
      task_id?: string
      agent_id?: string
      since?: string
      until?: string
      cursor?: string
    } = { limit: 200 }
    if (filters.taskId) params.task_id = filters.taskId
    if (filters.agentId) params.agent_id = filters.agentId
    if (filters.since) params.since = filters.since
    if (filters.until) params.until = filters.until
    if (cursor) params.cursor = cursor
    const response = await apiClient.get<PaginatedResponse<CoordinationMetricsRecord>>(
      '/coordination/metrics',
      { params },
    )
    return unwrapPaginated<CoordinationMetricsRecord>(response)
  })
}

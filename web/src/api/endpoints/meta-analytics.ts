/**
 * Cross-deployment analytics endpoints (read surface).
 *
 * Pattern and recommendation reads for the collector-role deployment.
 * The ``POST /meta/analytics/events`` ingestion path is backend-to-
 * backend only and not surfaced in the dashboard; see the meta-analytics
 * controller docstring.
 */
import { apiClient, paginateAll, unwrapPaginated } from '../client'
import type { PaginatedResponse } from '../types/http'
import type { AggregatedPattern, ThresholdRecommendation } from '../types'

/** Query aggregated cross-deployment patterns (newest first). */
export async function listPatterns(
  minDeployments?: number,
): Promise<readonly AggregatedPattern[]> {
  return paginateAll<AggregatedPattern>(async (cursor) => {
    const params: { limit: number; min_deployments?: number; cursor?: string } = { limit: 200 }
    if (minDeployments != null) params.min_deployments = minDeployments
    if (cursor) params.cursor = cursor
    const response = await apiClient.get<PaginatedResponse<AggregatedPattern>>(
      '/meta/analytics/patterns',
      { params },
    )
    return unwrapPaginated<AggregatedPattern>(response)
  })
}

/** Threshold recommendations derived from aggregated data. */
export async function listRecommendations(): Promise<readonly ThresholdRecommendation[]> {
  return paginateAll<ThresholdRecommendation>(async (cursor) => {
    const params: { limit: number; cursor?: string } = { limit: 200 }
    if (cursor) params.cursor = cursor
    const response = await apiClient.get<PaginatedResponse<ThresholdRecommendation>>(
      '/meta/analytics/recommendations',
      { params },
    )
    return unwrapPaginated<ThresholdRecommendation>(response)
  })
}

import { apiClient, unwrap } from '../client'
import type { ApiResponse, OverviewMetrics } from '../types'

export async function getOverviewMetrics(): Promise<OverviewMetrics> {
  const response = await apiClient.get<ApiResponse<OverviewMetrics>>('/analytics/overview')
  return unwrap(response)
}

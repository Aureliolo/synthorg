import { apiClient, unwrap } from '../client'
import type { OverviewMetrics } from '../types'

export async function getOverviewMetrics(): Promise<OverviewMetrics> {
  const response = await apiClient.get('/analytics/overview')
  return unwrap(response)
}

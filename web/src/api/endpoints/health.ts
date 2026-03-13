import { apiClient, unwrap } from '../client'
import type { ApiResponse, HealthStatus } from '../types'

export async function getHealth(): Promise<HealthStatus> {
  const response = await apiClient.get<ApiResponse<HealthStatus>>('/health')
  return unwrap(response)
}

import { apiClient, unwrap } from '../client'
import type { HealthStatus } from '../types'

export async function getHealth(): Promise<HealthStatus> {
  const response = await apiClient.get('/health')
  return unwrap(response)
}

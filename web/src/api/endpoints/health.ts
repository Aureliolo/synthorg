import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type { LivenessStatus, ReadinessStatus } from '../types/system'

/**
 * Liveness probe -- always returns 200 while the backend process is
 * alive. Used by supervisors to decide whether to restart the pod.
 */
export async function getLiveness(): Promise<LivenessStatus> {
  const response = await apiClient.get<ApiResponse<LivenessStatus>>('/healthz')
  return unwrap(response)
}

/**
 * Readiness probe -- returns 200 when persistence + message bus are
 * healthy, 503 otherwise. Used by load-balancers to gate traffic.
 */
export async function getReadiness(): Promise<ReadinessStatus> {
  const response = await apiClient.get<ApiResponse<ReadinessStatus>>('/readyz')
  return unwrap(response)
}

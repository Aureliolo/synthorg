import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  LivenessStatus,
  ReadinessProbe,
  ReadinessStatus,
} from '../types/system'

/**
 * Liveness probe -- always returns 200 while the backend process is
 * alive. Used by supervisors to decide whether to restart the pod.
 */
export async function getLiveness(): Promise<LivenessStatus> {
  const response = await apiClient.get<ApiResponse<LivenessStatus>>('/healthz')
  return unwrap(response)
}

/**
 * Readiness probe -- 200 when every configured dependency is healthy,
 * 503 otherwise. Used by load-balancers to gate traffic. The body is
 * deliberately topology-free (binary outcome + version + uptime); the
 * per-component breakdown lives behind authentication on `/health`.
 */
export async function getReadiness(): Promise<ReadinessProbe> {
  const response = await apiClient.get<ApiResponse<ReadinessProbe>>('/readyz')
  return unwrap(response)
}

/**
 * Authenticated component-health detail -- the full per-subsystem
 * breakdown (persistence / message bus / providers / telemetry) for the
 * dashboard health popover. 200 healthy / 503 unavailable. Requires a
 * read-access role; unauthenticated callers must use `getReadiness()`.
 */
export async function getHealthDetail(): Promise<ReadinessStatus> {
  const response = await apiClient.get<ApiResponse<ReadinessStatus>>('/health')
  return unwrap(response)
}

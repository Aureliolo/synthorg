import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type { SubsystemsResponse } from '../types/subsystems'

/**
 * Every declared subsystem's current phase, and why one is not up.
 *
 * The read is live but does not reconcile, so refreshing a dashboard cannot
 * itself cause a subsystem to activate. Each report carries `detail` and
 * `waiting_on`, which exist to answer "why is this not up" in the operator's
 * terms rather than sending them to the wiring log.
 */
export async function getSubsystems(): Promise<SubsystemsResponse> {
  const response = await apiClient.get<ApiResponse<SubsystemsResponse>>('/subsystems')
  return unwrap(response)
}

import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type { RestartResponse } from '../types/system'

/**
 * Restart the backend so a `restart_required` setting takes effect.
 *
 * Returns before the process goes away: the backend acknowledges, then signals
 * itself after `delay_seconds` so this response is written first. The caller is
 * expected to wait out the gap and poll liveness for the replacement process.
 *
 * 409 when nothing is configured to restart the process. That is the honest
 * answer rather than a failure to handle: exiting there would stop the
 * deployment and leave it stopped.
 */
export async function restartBackend(): Promise<RestartResponse> {
  const response = await apiClient.post<ApiResponse<RestartResponse>>(
    '/meta/restart',
    { confirm: true },
  )
  return unwrap(response)
}

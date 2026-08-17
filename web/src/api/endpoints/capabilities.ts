import { apiClient, unwrap } from '../client'
import type { Capabilities } from '../types/capabilities'
import type { ApiResponse } from '../types/http'

/**
 * Fetch the deployment's capability matrix.
 *
 * Most flags describe wiring fixed for the lifetime of the backend process,
 * which is why ``useCapabilities`` caches the result for the whole session.
 * The web-research flags are the exception: they resolve from settings an
 * operator can write while the dashboard is open, so a write to a
 * capability-bearing namespace re-reads this endpoint rather than waiting for
 * a reload.
 */
export async function getCapabilities(): Promise<Capabilities> {
  const response = await apiClient.get<ApiResponse<Capabilities>>(
    '/capabilities/',
  )
  return unwrap(response)
}

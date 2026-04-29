import { apiClient, unwrap } from '../client'
import type { Capabilities } from '../types/capabilities'
import type { ApiResponse } from '../types/http'

/**
 * Fetch the deployment's capability matrix.
 *
 * The matrix is static for the lifetime of the backend process
 * (changing it requires a restart) so the dashboard caches the
 * result for the whole session via ``useCapabilities``.
 */
export async function getCapabilities(): Promise<Capabilities> {
  const response = await apiClient.get<ApiResponse<Capabilities>>(
    '/capabilities/',
  )
  return unwrap(response)
}

import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '../../types/http'
import type {
  AddAllowlistEntryRequest,
  DiscoverModelsResponse,
  DiscoveryPolicyResponse,
  ProbeLocalResponse,
  ProviderHealthSummary,
  RemoveAllowlistEntryRequest,
} from '../../types/providers'

export async function getProviderHealth(name: string): Promise<ProviderHealthSummary> {
  const response = await apiClient.get<ApiResponse<ProviderHealthSummary>>(`/providers/${encodeURIComponent(name)}/health`)
  return unwrap(response)
}

/**
 * Probe every local preset's candidate URLs in one batch.
 *
 * Server-side fan-out via ``asyncio.TaskGroup``; a single rate-limit
 * slot is consumed per call. Per-preset failures are reported in the
 * ``errors`` envelope rather than raising.
 */
export async function probeLocal(): Promise<ProbeLocalResponse> {
  const response = await apiClient.post<ApiResponse<ProbeLocalResponse>>(
    '/providers/probe-local',
    {},
  )
  return unwrap(response)
}

export async function discoverModels(
  name: string,
  presetHint?: string,
): Promise<DiscoverModelsResponse> {
  const params = presetHint ? { preset_hint: presetHint } : undefined
  const response = await apiClient.post<ApiResponse<DiscoverModelsResponse>>(
    `/providers/${encodeURIComponent(name)}/discover-models`,
    undefined,
    { params },
  )
  return unwrap(response)
}

export async function getDiscoveryPolicy(): Promise<DiscoveryPolicyResponse> {
  const response = await apiClient.get<ApiResponse<DiscoveryPolicyResponse>>('/providers/discovery-policy')
  return unwrap(response)
}

export async function addAllowlistEntry(data: AddAllowlistEntryRequest): Promise<DiscoveryPolicyResponse> {
  const response = await apiClient.post<ApiResponse<DiscoveryPolicyResponse>>('/providers/discovery-policy/entries', data)
  return unwrap(response)
}

export async function removeAllowlistEntry(data: RemoveAllowlistEntryRequest): Promise<DiscoveryPolicyResponse> {
  const response = await apiClient.post<ApiResponse<DiscoveryPolicyResponse>>('/providers/discovery-policy/remove-entry', data)
  return unwrap(response)
}

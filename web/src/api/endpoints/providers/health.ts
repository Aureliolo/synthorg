import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '@/api/types/http'
import type {
  AddAllowlistEntryRequest,
  DiscoverModelsResponse,
  DiscoveryPolicyResponse,
  ModelServiceability,
  ProbeLocalResponse,
  ProviderHealthSummary,
  RemoveAllowlistEntryRequest,
} from '@/api/types/providers'

export async function getProviderHealth(name: string): Promise<ProviderHealthSummary> {
  const response = await apiClient.get<ApiResponse<ProviderHealthSummary>>(`/providers/${encodeURIComponent(name)}/health`)
  return unwrap(response)
}

/**
 * Call the provider now and return the health that call produces.
 *
 * The read endpoint can only replay what was already recorded, so a
 * provider whose fault an operator has just fixed keeps reporting it until
 * something calls it again.
 */
export async function recheckProviderHealth(name: string): Promise<ProviderHealthSummary> {
  const response = await apiClient.post<ApiResponse<ProviderHealthSummary>>(
    `/providers/${encodeURIComponent(name)}/health/recheck`,
    {},
  )
  return unwrap(response)
}

/** Call every configured provider now, returning each one's new health. */
export async function recheckAllProviderHealth(): Promise<Record<string, ProviderHealthSummary>> {
  const response = await apiClient.post<ApiResponse<Record<string, ProviderHealthSummary>>>(
    '/providers/health/recheck',
    {},
  )
  return unwrap(response)
}

/**
 * Read each model this provider has recently served.
 *
 * Distinct from health, which is per connection over 24 hours and counts a
 * reachability ping as evidence. A model queueing for an hour is invisible
 * there and visible here.
 */
export async function getProviderServiceability(name: string): Promise<ModelServiceability[]> {
  const response = await apiClient.get<ApiResponse<ModelServiceability[]>>(
    `/providers/${encodeURIComponent(name)}/serviceability`,
  )
  return unwrap(response)
}

/** Read every (provider, model) pair a real call has exercised recently. */
export async function getFleetServiceability(): Promise<ModelServiceability[]> {
  const response = await apiClient.get<ApiResponse<ModelServiceability[]>>('/providers/serviceability')
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

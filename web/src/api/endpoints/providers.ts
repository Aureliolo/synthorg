import { apiClient, unwrap } from '../client'
import type { ApiResponse, ProviderConfig, ProviderModelConfig } from '../types'

/** Strip api_key from a single provider config. */
function stripSecrets(raw: ProviderConfig & { api_key?: unknown }): ProviderConfig {
  const { api_key: _discarded, ...safe } = raw
  return safe
}

export async function listProviders(): Promise<Record<string, ProviderConfig>> {
  const response = await apiClient.get<ApiResponse<Record<string, ProviderConfig & { api_key?: unknown }>>>('/providers')
  const raw = unwrap<Record<string, ProviderConfig & { api_key?: unknown }>>(response)
  const result: Record<string, ProviderConfig> = Object.create(null) as Record<string, ProviderConfig>
  for (const [key, provider] of Object.entries(raw)) {
    // Skip prototype-polluting keys
    if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue
    result[key] = stripSecrets(provider)
  }
  return result
}

export async function getProvider(name: string): Promise<ProviderConfig> {
  const response = await apiClient.get<ApiResponse<ProviderConfig & { api_key?: unknown }>>(`/providers/${encodeURIComponent(name)}`)
  return stripSecrets(unwrap(response))
}

export async function getProviderModels(name: string): Promise<ProviderModelConfig[]> {
  const response = await apiClient.get<ApiResponse<ProviderModelConfig[]>>(`/providers/${encodeURIComponent(name)}/models`)
  return unwrap(response)
}

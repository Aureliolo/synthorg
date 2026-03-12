import { apiClient, unwrap } from '../client'
import type { ProviderConfig, ProviderModelConfig } from '../types'

export async function listProviders(): Promise<Record<string, ProviderConfig>> {
  const response = await apiClient.get('/providers')
  return unwrap(response)
}

export async function getProvider(name: string): Promise<ProviderConfig> {
  const response = await apiClient.get(`/providers/${name}`)
  return unwrap(response)
}

export async function getProviderModels(name: string): Promise<ProviderModelConfig[]> {
  const response = await apiClient.get(`/providers/${name}/models`)
  return unwrap(response)
}

import { createLogger } from '@/lib/logger'
import {
  apiClient,
  paginateAll,
  unwrap,
  unwrapPaginated,
  unwrapVoid,
} from '../../client'
import type { ApiResponse, PaginatedResponse } from '../../types/http'
import type {
  CreateFromPresetRequest,
  CreateProviderRequest,
  ProviderConfig,
  ProviderModelResponse,
  ProviderPreset,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateProviderRequest,
} from '../../types/providers'

const log = createLogger('providers-api')

const PROTOTYPE_POLLUTION_KEYS: readonly string[] = ['__proto__', 'constructor', 'prototype']

export async function listProviders(): Promise<Record<string, ProviderConfig>> {
  const all = await paginateAll<ProviderConfig>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const url = qs ? `/providers?${qs}` : '/providers'
    const response = await apiClient.get<PaginatedResponse<ProviderConfig>>(url)
    return unwrapPaginated<ProviderConfig>(response)
  })
  const result: Record<string, ProviderConfig> = Object.create(null)
  for (const provider of all) {
    const key = provider.name
    // Prototype-pollution keys are silently dropped (defence-in-depth
    // against a hostile or buggy backend); a missing ``name`` is
    // logged because the wire contract guarantees one on every
    // paginated entry, so its absence is a regression worth surfacing.
    if (!key) {
      log.warn('Skipping provider with missing name in paginated response')
      continue
    }
    if (PROTOTYPE_POLLUTION_KEYS.includes(key)) continue
    result[key] = provider
  }
  return result
}

export async function getProvider(name: string): Promise<ProviderConfig> {
  const response = await apiClient.get<ApiResponse<ProviderConfig>>(`/providers/${encodeURIComponent(name)}`)
  return unwrap(response)
}

export async function getProviderModels(name: string): Promise<ProviderModelResponse[]> {
  return paginateAll<ProviderModelResponse>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const base = `/providers/${encodeURIComponent(name)}/models`
    const url = qs ? `${base}?${qs}` : base
    const response = await apiClient.get<PaginatedResponse<ProviderModelResponse>>(url)
    return unwrapPaginated<ProviderModelResponse>(response)
  })
}

export async function createProvider(data: CreateProviderRequest): Promise<ProviderConfig> {
  const response = await apiClient.post<ApiResponse<ProviderConfig>>('/providers', data)
  return unwrap(response)
}

export async function updateProvider(name: string, data: UpdateProviderRequest): Promise<ProviderConfig> {
  const response = await apiClient.put<ApiResponse<ProviderConfig>>(`/providers/${encodeURIComponent(name)}`, data)
  return unwrap(response)
}

export async function deleteProvider(name: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(`/providers/${encodeURIComponent(name)}`)
  unwrapVoid(response)
}

export async function testConnection(name: string, data?: TestConnectionRequest): Promise<TestConnectionResponse> {
  // Extended timeout: local providers (Ollama) may need to load models into memory.
  const response = await apiClient.post<ApiResponse<TestConnectionResponse>>(
    `/providers/${encodeURIComponent(name)}/test`,
    data ?? {},
    { timeout: 120_000 },
  )
  return unwrap(response)
}

export async function listPresets(): Promise<ProviderPreset[]> {
  const response = await apiClient.get<ApiResponse<ProviderPreset[]>>('/providers/presets')
  return unwrap(response)
}

export async function createFromPreset(data: CreateFromPresetRequest): Promise<ProviderConfig> {
  const response = await apiClient.post<ApiResponse<ProviderConfig>>('/providers/from-preset', data)
  return unwrap(response)
}

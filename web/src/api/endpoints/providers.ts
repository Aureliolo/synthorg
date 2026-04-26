import { createLogger } from '@/lib/logger'
import { getCsrfToken } from '@/utils/csrf'
import { IS_DEV_AUTH_BYPASS } from '@/utils/dev'
import { apiClient, unwrap, unwrapVoid } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  AddAllowlistEntryRequest,
  CreateFromPresetRequest,
  CreateProviderRequest,
  DiscoverModelsResponse,
  DiscoveryPolicyResponse,
  LocalModelParams,
  ProbeLocalResponse,
  ProviderConfig,
  ProviderHealthSummary,
  ProviderModelResponse,
  ProviderPreset,
  PullModelRequest,
  PullProgressEvent,
  RemoveAllowlistEntryRequest,
  TestConnectionRequest,
  TestConnectionResponse,
  UpdateModelConfigRequest,
  UpdateProviderRequest,
} from '../types/providers'

const log = createLogger('providers-api')

export async function listProviders(): Promise<Record<string, ProviderConfig>> {
  const response = await apiClient.get<ApiResponse<Record<string, ProviderConfig>>>('/providers')
  const raw = unwrap<Record<string, ProviderConfig>>(response)
  const result: Record<string, ProviderConfig> = Object.create(null) as Record<string, ProviderConfig>
  for (const [key, provider] of Object.entries(raw)) {
    if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue
    result[key] = provider
  }
  return result
}

export async function getProvider(name: string): Promise<ProviderConfig> {
  const response = await apiClient.get<ApiResponse<ProviderConfig>>(`/providers/${encodeURIComponent(name)}`)
  return unwrap(response)
}

export async function getProviderModels(name: string): Promise<ProviderModelResponse[]> {
  const response = await apiClient.get<ApiResponse<ProviderModelResponse[]>>(`/providers/${encodeURIComponent(name)}/models`)
  return unwrap(response)
}

export async function getProviderHealth(name: string): Promise<ProviderHealthSummary> {
  const response = await apiClient.get<ApiResponse<ProviderHealthSummary>>(`/providers/${encodeURIComponent(name)}/health`)
  return unwrap(response)
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
  // Extended timeout: local providers (Ollama) may need to load models into memory
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

/**
 * Probe every local preset's candidate URLs in one batch.
 *
 * Server-side fan-out via ``asyncio.TaskGroup``; a single rate-limit
 * slot is consumed per call.  Per-preset failures are reported in the
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

/** Encode a model ID for use in URL paths, preserving `/` for :path params. */
function encodeModelIdPath(modelId: string): string {
  return modelId.split('/').map(encodeURIComponent).join('/')
}

/** Process buffered SSE lines, dispatching events to the callback. */
function processSseLines(
  lines: string[],
  state: { currentEvent: string },
  onProgress: (event: PullProgressEvent) => void,
): void {
  for (const line of lines) {
    if (line.startsWith('event: ')) {
      state.currentEvent = line.slice(7).trim()
    } else if (line.startsWith('data: ')) {
      try {
        const parsed = JSON.parse(line.slice(6)) as PullProgressEvent
        if (state.currentEvent === 'error' || parsed.error) {
          const errorMsg = parsed.error ?? parsed.status ?? 'Pull failed'
          state.currentEvent = ''
          onProgress(parsed)
          throw new Error(errorMsg)
        }
        onProgress(parsed)
      } catch (err) {
        state.currentEvent = ''
        if (err instanceof SyntaxError) {
          log.warn('Malformed JSON in pull stream line')
          continue
        }
        if (err instanceof Error) throw err
      }
      state.currentEvent = ''
    }
  }
}

/**
 * Pull a model on a local provider via SSE streaming.
 *
 * Uses fetch + ReadableStream because the endpoint is POST-based
 * and EventSource only supports GET.
 */
export async function pullModel(
  name: string,
  modelName: string,
  onProgress: (event: PullProgressEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const baseUrl = apiClient.defaults.baseURL ?? ''
  const url = `${baseUrl}/providers/${encodeURIComponent(name)}/models/pull`

  const csrfToken = getCsrfToken()
  const response = await fetch(url, {
    method: 'POST',
    credentials: 'include',
    headers: {
      'Content-Type': 'application/json',
      ...(csrfToken ? { 'X-CSRF-Token': csrfToken } : {}),
    },
    body: JSON.stringify({ model_name: modelName } satisfies PullModelRequest),
    signal,
  })

  if (!response.ok || !response.body) {
    if (response.status === 401 && !IS_DEV_AUTH_BYPASS) {
      // Server clears the session cookie. Sync Zustand auth state.
      import('@/stores/auth').then(({ useAuthStore }) => {
        useAuthStore.getState().handleUnauthorized()
      }).catch((importErr: unknown) => {
        log.error('Auth store cleanup failed during SSE 401 handling:', importErr)
        if (window.location.pathname !== '/login' && window.location.pathname !== '/setup') {
          window.location.href = '/login'
        }
      })
    }
    throw new Error(`Pull failed: HTTP ${response.status}`)
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let receivedDone = false
  const sseState = { currentEvent: '' }

  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })

    const lines = buffer.split('\n')
    buffer = lines.pop() ?? ''

    processSseLines(lines, sseState, (event) => {
      if (event.done) receivedDone = true
      onProgress(event)
    })
  }

  // Process any remaining data in the buffer after the stream ends
  buffer += decoder.decode()
  if (buffer.trim()) {
    const trailing = buffer.split('\n')
    processSseLines(trailing, sseState, (event) => {
      if (event.done) receivedDone = true
      onProgress(event)
    })
  }

  if (!receivedDone) {
    throw new Error('Pull stream ended without completion event')
  }
}

export async function deleteModel(name: string, modelId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/providers/${encodeURIComponent(name)}/models/${encodeModelIdPath(modelId)}`,
  )
  unwrapVoid(response)
}

export async function updateModelConfig(
  name: string,
  modelId: string,
  params: LocalModelParams,
): Promise<ProviderModelResponse> {
  const payload: UpdateModelConfigRequest = { local_params: params }
  const response = await apiClient.put<ApiResponse<ProviderModelResponse>>(
    `/providers/${encodeURIComponent(name)}/models/${encodeModelIdPath(modelId)}/config`,
    payload,
  )
  return unwrap(response)
}

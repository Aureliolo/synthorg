import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  AvailableLocalesResponse,
  PersonalityPresetInfo,
  SetupAgentRequest,
  SetupAgentResponse,
  SetupAgentSummary,
  SetupCompanyRequest,
  SetupCompanyResponse,
  SetupNameLocalesRequest,
  SetupNameLocalesResponse,
  SetupStatusResponse,
  TemplateInfoResponse,
  UpdateAgentModelRequest,
  UpdateAgentNameRequest,
  UpdateAgentPersonalityRequest,
} from '../types/setup'
import type { SetupCompleteResponse } from '@/api/types'

export async function getSetupStatus(): Promise<SetupStatusResponse> {
  const response = await apiClient.get<ApiResponse<SetupStatusResponse>>('/setup/status')
  return unwrap(response)
}

export async function listTemplates(): Promise<TemplateInfoResponse[]> {
  const response = await apiClient.get<ApiResponse<TemplateInfoResponse[]>>('/setup/templates')
  return unwrap(response)
}

export async function createCompany(data: SetupCompanyRequest): Promise<SetupCompanyResponse> {
  const response = await apiClient.post<ApiResponse<SetupCompanyResponse>>('/setup/company', data)
  return unwrap(response)
}

export async function createAgent(data: SetupAgentRequest): Promise<SetupAgentResponse> {
  const response = await apiClient.post<ApiResponse<SetupAgentResponse>>('/setup/agent', data)
  return unwrap(response)
}

export async function getAgents(): Promise<readonly SetupAgentSummary[]> {
  return paginateAll<SetupAgentSummary>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const url = qs ? `/setup/agents?${qs}` : '/setup/agents'
    const response = await apiClient.get<PaginatedResponse<SetupAgentSummary>>(url)
    return unwrapPaginated<SetupAgentSummary>(response)
  })
}

export async function updateAgentModel(
  index: number,
  data: UpdateAgentModelRequest,
): Promise<SetupAgentSummary> {
  if (!Number.isInteger(index) || index < 0) {
    throw new Error(`Invalid agent index: ${index}`)
  }
  const response = await apiClient.put<ApiResponse<SetupAgentSummary>>(
    `/setup/agents/${index}/model`,
    data,
  )
  return unwrap(response)
}

export async function updateAgentName(
  index: number,
  data: UpdateAgentNameRequest,
): Promise<SetupAgentSummary> {
  if (!Number.isInteger(index) || index < 0) {
    throw new Error(`Invalid agent index: ${index}`)
  }
  const response = await apiClient.put<ApiResponse<SetupAgentSummary>>(
    `/setup/agents/${index}/name`,
    data,
  )
  return unwrap(response)
}

export async function randomizeAgentName(
  index: number,
): Promise<SetupAgentSummary> {
  if (!Number.isInteger(index) || index < 0) {
    throw new Error(`Invalid agent index: ${index}`)
  }
  const response = await apiClient.post<ApiResponse<SetupAgentSummary>>(
    `/setup/agents/${index}/randomize-name`,
  )
  return unwrap(response)
}

export async function updateAgentPersonality(
  index: number,
  data: UpdateAgentPersonalityRequest,
): Promise<SetupAgentSummary> {
  if (!Number.isInteger(index) || index < 0) {
    throw new Error(`Invalid agent index: ${index}`)
  }
  const response = await apiClient.put<ApiResponse<SetupAgentSummary>>(
    `/setup/agents/${index}/personality`,
    data,
  )
  return unwrap(response)
}

export async function listPersonalityPresets(): Promise<readonly PersonalityPresetInfo[]> {
  return paginateAll<PersonalityPresetInfo>(async (cursor) => {
    const params = new URLSearchParams()
    if (cursor) params.set('cursor', cursor)
    const qs = params.toString()
    const url = qs ? `/setup/personality-presets?${qs}` : '/setup/personality-presets'
    const response = await apiClient.get<PaginatedResponse<PersonalityPresetInfo>>(url)
    return unwrapPaginated<PersonalityPresetInfo>(response)
  })
}

export async function getAvailableLocales(): Promise<AvailableLocalesResponse> {
  const response = await apiClient.get<ApiResponse<AvailableLocalesResponse>>('/setup/name-locales/available')
  return unwrap(response)
}

export async function getNameLocales(): Promise<SetupNameLocalesResponse> {
  const response = await apiClient.get<ApiResponse<SetupNameLocalesResponse>>('/setup/name-locales')
  return unwrap(response)
}

export async function saveNameLocales(data: SetupNameLocalesRequest): Promise<SetupNameLocalesResponse> {
  const response = await apiClient.put<ApiResponse<SetupNameLocalesResponse>>('/setup/name-locales', data)
  return unwrap(response)
}

export async function completeSetup(): Promise<SetupCompleteResponse> {
  const response = await apiClient.post<ApiResponse<SetupCompleteResponse>>('/setup/complete')
  return unwrap(response)
}

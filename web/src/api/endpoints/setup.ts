import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  AvailableLocalesResponse,
  SetupAgentRequest,
  SetupAgentResponse,
  SetupAgentSummary,
  SetupCompanyRequest,
  SetupCompanyResponse,
  SetupModelRecommendationsResponse,
  SetupNameLocalesRequest,
  SetupNameLocalesResponse,
  SetupStatusResponse,
  TemplateInfoResponse,
  UpdateAgentModelRequest,
  UpdateAgentNameRequest,
} from '../types/setup'
import type { SetupCompleteResponse } from '@/api/types/setup'

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

/**
 * Fetch the persisted company so a resumed wizard (or any client) can rehydrate
 * the company + applied template from the backend rather than a client-side
 * copy. 404s when no company has been created yet.
 */
export async function getCompany(): Promise<SetupCompanyResponse> {
  const response = await apiClient.get<ApiResponse<SetupCompanyResponse>>('/setup/company')
  return unwrap(response)
}

/**
 * Fetch the wizard's recommended coordinator (decomposition) and memory
 * (embedding) models plus the candidate lists to override them with. The
 * wizard prefills the recommendations and writes any override through the
 * settings API; completion only auto-selects values the operator left unset.
 */
export async function getModelRecommendations(): Promise<SetupModelRecommendationsResponse> {
  const response = await apiClient.get<ApiResponse<SetupModelRecommendationsResponse>>(
    '/setup/model-recommendations',
  )
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

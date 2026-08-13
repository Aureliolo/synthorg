import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '@/api/types/http'
import type {
  ApplyRecommendationRequest,
  ClassifierModelDTO,
  CapabilityAssignmentsResponse,
  CapabilityOverrideRequest,
  CapabilityRecommendationsResponse,
} from '@/api/types/providers'

const BASE = '/providers/capability-assignments'

export async function listCapabilityAssignments(): Promise<CapabilityAssignmentsResponse> {
  const response = await apiClient.get<ApiResponse<CapabilityAssignmentsResponse>>(BASE)
  return unwrap(response)
}

export async function setTierOverride(
  provider: string,
  modelId: string,
  data: CapabilityOverrideRequest,
): Promise<CapabilityAssignmentsResponse> {
  const response = await apiClient.put<ApiResponse<CapabilityAssignmentsResponse>>(
    `${BASE}/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}`,
    data,
  )
  return unwrap(response)
}

export async function recommendCapabilityLevel(
  provider: string,
  modelId: string,
): Promise<CapabilityRecommendationsResponse> {
  const response = await apiClient.post<ApiResponse<CapabilityRecommendationsResponse>>(
    `${BASE}/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}/recommend`,
    {},
  )
  return unwrap(response)
}

export async function recommendAllTiers(): Promise<CapabilityRecommendationsResponse> {
  const response = await apiClient.post<ApiResponse<CapabilityRecommendationsResponse>>(
    `${BASE}/recommend-all`,
    {},
  )
  return unwrap(response)
}

export async function applyCapabilityRecommendation(
  data: ApplyRecommendationRequest,
): Promise<CapabilityAssignmentsResponse> {
  const response = await apiClient.post<ApiResponse<CapabilityAssignmentsResponse>>(
    `${BASE}/apply`,
    data,
  )
  return unwrap(response)
}

export async function getTierClassifierModel(): Promise<ClassifierModelDTO> {
  const response = await apiClient.get<ApiResponse<ClassifierModelDTO>>(
    `${BASE}/classifier-model`,
  )
  return unwrap(response)
}

export async function setTierClassifierModel(
  data: ClassifierModelDTO,
): Promise<ClassifierModelDTO> {
  const response = await apiClient.put<ApiResponse<ClassifierModelDTO>>(
    `${BASE}/classifier-model`,
    data,
  )
  return unwrap(response)
}

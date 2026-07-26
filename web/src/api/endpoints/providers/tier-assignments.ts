import { apiClient, unwrap } from '../../client'
import type { ApiResponse } from '@/api/types/http'
import type {
  ApplyRecommendationRequest,
  ClassifierModelDTO,
  TierAssignmentsResponse,
  TierOverrideRequest,
  TierRecommendationsResponse,
} from '@/api/types/providers'

const BASE = '/providers/tier-assignments'

export async function listTierAssignments(): Promise<TierAssignmentsResponse> {
  const response = await apiClient.get<ApiResponse<TierAssignmentsResponse>>(BASE)
  return unwrap(response)
}

export async function setTierOverride(
  provider: string,
  modelId: string,
  data: TierOverrideRequest,
): Promise<TierAssignmentsResponse> {
  const response = await apiClient.put<ApiResponse<TierAssignmentsResponse>>(
    `${BASE}/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}`,
    data,
  )
  return unwrap(response)
}

export async function recommendModelTier(
  provider: string,
  modelId: string,
): Promise<TierRecommendationsResponse> {
  const response = await apiClient.post<ApiResponse<TierRecommendationsResponse>>(
    `${BASE}/${encodeURIComponent(provider)}/${encodeURIComponent(modelId)}/recommend`,
    {},
  )
  return unwrap(response)
}

export async function recommendAllTiers(): Promise<TierRecommendationsResponse> {
  const response = await apiClient.post<ApiResponse<TierRecommendationsResponse>>(
    `${BASE}/recommend-all`,
    {},
  )
  return unwrap(response)
}

export async function applyTierRecommendation(
  data: ApplyRecommendationRequest,
): Promise<TierAssignmentsResponse> {
  const response = await apiClient.post<ApiResponse<TierAssignmentsResponse>>(
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

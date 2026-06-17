import { apiClient, unwrap } from '../client'
import type {
  DecideRecommendationRequest,
  RefreshCycleReportDTO,
  RefreshStatusDTO,
  UpgradeRecommendationDTO,
} from '../types'
import type { ApiResponse } from '../types/http'
import type { RecommendationStatus } from '../types/enum-values.gen'

const BASE = '/providers/model-refresh'

export async function listModelRecommendations(
  status?: RecommendationStatus,
): Promise<readonly UpgradeRecommendationDTO[]> {
  const response = await apiClient.get<ApiResponse<readonly UpgradeRecommendationDTO[]>>(
    `${BASE}/recommendations`,
    { params: status !== undefined ? { status } : undefined },
  )
  return unwrap(response)
}

export async function approveRecommendation(
  id: string,
  decidedBy: string,
): Promise<UpgradeRecommendationDTO> {
  const body: DecideRecommendationRequest = { decided_by: decidedBy }
  const response = await apiClient.post<ApiResponse<UpgradeRecommendationDTO>>(
    `${BASE}/recommendations/${encodeURIComponent(id)}/approve`,
    body,
  )
  return unwrap(response)
}

export async function rejectRecommendation(
  id: string,
  decidedBy: string,
): Promise<UpgradeRecommendationDTO> {
  const body: DecideRecommendationRequest = { decided_by: decidedBy }
  const response = await apiClient.post<ApiResponse<UpgradeRecommendationDTO>>(
    `${BASE}/recommendations/${encodeURIComponent(id)}/reject`,
    body,
  )
  return unwrap(response)
}

export async function triggerRefresh(): Promise<RefreshCycleReportDTO> {
  const response = await apiClient.post<ApiResponse<RefreshCycleReportDTO>>(
    `${BASE}/refresh`,
  )
  return unwrap(response)
}

export async function getRefreshStatus(): Promise<RefreshStatusDTO> {
  const response = await apiClient.get<ApiResponse<RefreshStatusDTO>>(`${BASE}/status`)
  return unwrap(response)
}

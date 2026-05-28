import { apiClient, unwrap, unwrapVoid } from '../client'
import type {
  CalibrationSummaryResponse,
  CollaborationScoreResult,
  OverrideResponse,
  SetOverrideRequest,
} from '../types/collaboration'
import type { ApiResponse } from '../types/http'

const basePath = (agentId: string) =>
  `/agents/${encodeURIComponent(agentId)}/collaboration`

export async function getCollaborationScore(agentId: string): Promise<CollaborationScoreResult> {
  const response = await apiClient.get<ApiResponse<CollaborationScoreResult>>(
    `${basePath(agentId)}/score`,
  )
  return unwrap(response)
}

export async function getOverride(agentId: string): Promise<OverrideResponse> {
  const response = await apiClient.get<ApiResponse<OverrideResponse>>(
    `${basePath(agentId)}/override`,
  )
  return unwrap(response)
}

export async function setOverride(
  agentId: string,
  data: SetOverrideRequest,
): Promise<OverrideResponse> {
  const response = await apiClient.post<ApiResponse<OverrideResponse>>(
    `${basePath(agentId)}/override`,
    data,
  )
  return unwrap(response)
}

export async function clearOverride(agentId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(`${basePath(agentId)}/override`)
  unwrapVoid(response)
}

export async function getCalibration(agentId: string): Promise<CalibrationSummaryResponse> {
  const response = await apiClient.get<ApiResponse<CalibrationSummaryResponse>>(
    `${basePath(agentId)}/calibration`,
  )
  return unwrap(response)
}

import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  ContentType,
  CreateTrainingPlanRequest,
  TrainingPlanResponse,
  TrainingPlanStatus,
  TrainingResultResponse,
  UpdateTrainingOverridesRequest,
} from '../types'

// -- Types -----------------------------------------------------------

export type {
  ContentType,
  CreateTrainingPlanRequest,
  TrainingPlanResponse,
  TrainingPlanStatus,
  TrainingResultResponse,
  UpdateTrainingOverridesRequest,
}

// -- Endpoints -------------------------------------------------------

export async function createTrainingPlan(
  agentId: string,
  data: CreateTrainingPlanRequest,
): Promise<TrainingPlanResponse> {
  const response = await apiClient.post<ApiResponse<TrainingPlanResponse>>(
    `/agents/${encodeURIComponent(agentId)}/training/plan`,
    data,
  )
  return unwrap(response)
}

export async function executeTrainingPlan(
  agentId: string,
): Promise<TrainingResultResponse> {
  const response = await apiClient.post<ApiResponse<TrainingResultResponse>>(
    `/agents/${encodeURIComponent(agentId)}/training/execute`,
  )
  return unwrap(response)
}

export async function getTrainingResult(
  agentId: string,
): Promise<TrainingResultResponse> {
  const response = await apiClient.get<ApiResponse<TrainingResultResponse>>(
    `/agents/${encodeURIComponent(agentId)}/training/result`,
  )
  return unwrap(response)
}

export async function getLatestTrainingPlan(
  agentId: string,
): Promise<TrainingPlanResponse> {
  const response = await apiClient.get<ApiResponse<TrainingPlanResponse>>(
    `/agents/${encodeURIComponent(agentId)}/training/plan`,
  )
  return unwrap(response)
}

export async function previewTrainingPlan(
  agentId: string,
): Promise<TrainingResultResponse> {
  const response = await apiClient.post<ApiResponse<TrainingResultResponse>>(
    `/agents/${encodeURIComponent(agentId)}/training/preview`,
  )
  return unwrap(response)
}

export async function updateTrainingOverrides(
  agentId: string,
  planId: string,
  data: UpdateTrainingOverridesRequest,
): Promise<TrainingPlanResponse> {
  const response = await apiClient.put<ApiResponse<TrainingPlanResponse>>(
    `/agents/${encodeURIComponent(agentId)}/training/plan/${encodeURIComponent(planId)}/overrides`,
    data,
  )
  return unwrap(response)
}

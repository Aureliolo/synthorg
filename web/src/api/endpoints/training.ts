import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type {
  ContentType,
  CreateTrainingPlanRequest as CreateTrainingPlanRequestWire,
  TrainingPlanResponse,
  TrainingPlanStatus,
  TrainingResultResponse,
  UpdateTrainingOverridesRequest,
} from '../types'

// -- Types -----------------------------------------------------------

/**
 * Create-plan request with the server-defaulted fields restored to optional.
 * The generator marks ``override_sources`` / ``require_review`` /
 * ``skip_training`` required because each has a backend ``@default``, so the
 * raw wire type would force every caller to send them; the backend accepts a
 * request that omits them.
 */
export type CreateTrainingPlanRequest = Omit<
  CreateTrainingPlanRequestWire,
  'override_sources' | 'require_review' | 'skip_training'
> & {
  readonly override_sources?: readonly string[]
  readonly require_review?: boolean
  readonly skip_training?: boolean
}

export type {
  ContentType,
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

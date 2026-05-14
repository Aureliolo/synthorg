import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'

// -- Types -----------------------------------------------------------

export type ContentType = 'procedural' | 'semantic' | 'tool_patterns'

export type TrainingPlanStatus = 'pending' | 'executed' | 'failed'

export interface TrainingPlanRequest {
  override_sources: string[]
  content_types?: ContentType[]
  custom_caps?: Partial<Record<ContentType, number>>
  skip_training?: boolean
  require_review?: boolean
}

export interface TrainingPlanResponse {
  id: string
  new_agent_id: string
  new_agent_role: string
  source_selector_type: string
  enabled_content_types: ContentType[]
  curation_strategy_type: string
  volume_caps: Array<[ContentType, number]>
  override_sources: string[]
  skip_training: boolean
  require_review: boolean
  status: TrainingPlanStatus
  created_at: string
  executed_at: string | null
}

export interface TrainingResultResponse {
  id: string
  plan_id: string
  new_agent_id: string
  source_agents_used: string[]
  items_extracted: Array<[ContentType, number]>
  items_after_curation: Array<[ContentType, number]>
  items_after_guards: Array<[ContentType, number]>
  items_stored: Array<[ContentType, number]>
  approval_item_id: string | null
  pending_approvals: ReadonlyArray<readonly [string, ContentType, number]>
  review_pending: boolean
  errors: string[]
  started_at: string
  completed_at: string
}

export interface TrainingOverridesRequest {
  override_sources?: string[]
  custom_caps?: Partial<Record<ContentType, number>>
}

// -- Endpoints -------------------------------------------------------

export async function createTrainingPlan(
  agentName: string,
  data: TrainingPlanRequest,
): Promise<TrainingPlanResponse> {
  const response = await apiClient.post<ApiResponse<TrainingPlanResponse>>(
    `/agents/${encodeURIComponent(agentName)}/training/plan`,
    data,
  )
  return unwrap(response)
}

export async function executeTrainingPlan(
  agentName: string,
): Promise<TrainingResultResponse> {
  const response = await apiClient.post<ApiResponse<TrainingResultResponse>>(
    `/agents/${encodeURIComponent(agentName)}/training/execute`,
  )
  return unwrap(response)
}

export async function getTrainingResult(
  agentName: string,
): Promise<TrainingResultResponse> {
  const response = await apiClient.get<ApiResponse<TrainingResultResponse>>(
    `/agents/${encodeURIComponent(agentName)}/training/result`,
  )
  return unwrap(response)
}

export async function getLatestTrainingPlan(
  agentName: string,
): Promise<TrainingPlanResponse> {
  const response = await apiClient.get<ApiResponse<TrainingPlanResponse>>(
    `/agents/${encodeURIComponent(agentName)}/training/plan`,
  )
  return unwrap(response)
}

export async function previewTrainingPlan(
  agentName: string,
): Promise<TrainingResultResponse> {
  const response = await apiClient.post<ApiResponse<TrainingResultResponse>>(
    `/agents/${encodeURIComponent(agentName)}/training/preview`,
  )
  return unwrap(response)
}

export async function updateTrainingOverrides(
  agentName: string,
  planId: string,
  data: TrainingOverridesRequest,
): Promise<TrainingPlanResponse> {
  const response = await apiClient.put<ApiResponse<TrainingPlanResponse>>(
    `/agents/${encodeURIComponent(agentName)}/training/plan/${encodeURIComponent(planId)}/overrides`,
    data,
  )
  return unwrap(response)
}

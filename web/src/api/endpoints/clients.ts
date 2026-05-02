import axios from 'axios'

import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'

// ── Types ───────────────────────────────────────────────────────

export interface ClientProfile {
  client_id: string
  name: string
  persona: string
  expertise_domains: readonly string[]
  strictness_level: number
}

export interface TaskRequirement {
  title: string
  description: string
  task_type: string
  priority: string
  estimated_complexity: string
  acceptance_criteria?: readonly string[]
}

export type RequestStatus =
  | 'submitted'
  | 'triaging'
  | 'scoping'
  | 'approved'
  | 'task_created'
  | 'cancelled'

export interface ClientRequest {
  request_id: string
  client_id: string
  requirement: TaskRequirement
  status: RequestStatus
  created_at: string
  metadata: Record<string, unknown>
}

export interface SimulationConfig {
  simulation_id: string
  project_id: string
  rounds: number
  clients_per_round: number
  requirements_per_client: number
}

export interface SimulationMetrics {
  total_requirements: number
  total_tasks_created: number
  tasks_accepted: number
  tasks_rejected: number
  tasks_reworked: number
  avg_review_rounds: number
  round_metrics: readonly Record<string, unknown>[]
  acceptance_rate: number
  rework_rate: number
}

export interface SimulationStatus {
  simulation_id: string
  status: string
  config: SimulationConfig
  metrics: SimulationMetrics
  progress: number
  started_at: string | null
  completed_at: string | null
  error: string | null
}

export interface ReviewStageResult {
  stage_name: string
  verdict: 'pass' | 'fail' | 'skip'
  reason: string | null
  duration_ms: number
  metadata: Record<string, unknown>
}

export interface PipelineResult {
  task_id: string
  final_verdict: 'pass' | 'fail' | 'skip'
  stage_results: readonly ReviewStageResult[]
  total_duration_ms: number
  reviewed_at: string
}

// ── Clients ─────────────────────────────────────────────────────

export interface CreateClientRequestBody {
  client_id: string
  name: string
  persona: string
  expertise_domains?: readonly string[]
  strictness_level?: number
}

export interface UpdateClientRequestBody {
  name?: string
  persona?: string
  expertise_domains?: readonly string[]
  strictness_level?: number
}

export async function listClients(
  params?: PaginationParams,
): Promise<PaginatedResult<ClientProfile>> {
  const response = await apiClient.get<PaginatedResponse<ClientProfile>>(
    '/clients',
    { params },
  )
  return unwrapPaginated<ClientProfile>(response)
}

export async function getClient(clientId: string): Promise<ClientProfile> {
  const response = await apiClient.get<ApiResponse<ClientProfile>>(
    `/clients/${encodeURIComponent(clientId)}`,
  )
  return unwrap(response)
}

export async function createClient(
  data: CreateClientRequestBody,
): Promise<ClientProfile> {
  const response = await apiClient.post<ApiResponse<ClientProfile>>(
    '/clients/',
    data,
  )
  return unwrap(response)
}

export async function updateClient(
  clientId: string,
  data: UpdateClientRequestBody,
): Promise<ClientProfile> {
  const response = await apiClient.patch<ApiResponse<ClientProfile>>(
    `/clients/${encodeURIComponent(clientId)}`,
    data,
  )
  return unwrap(response)
}

export async function deleteClient(clientId: string): Promise<void> {
  await apiClient.delete(`/clients/${encodeURIComponent(clientId)}`)
}

export interface SatisfactionPoint {
  feedback_id: string
  task_id: string
  accepted: boolean
  score: number
  created_at: string
}

export interface SatisfactionHistory {
  client_id: string
  total_reviews: number
  acceptance_rate: number
  average_score: number
  history: readonly SatisfactionPoint[]
}

export async function getClientSatisfaction(
  clientId: string,
): Promise<SatisfactionHistory> {
  const response = await apiClient.get<ApiResponse<SatisfactionHistory>>(
    `/clients/${encodeURIComponent(clientId)}/satisfaction`,
  )
  return unwrap(response)
}

// ── Requests ────────────────────────────────────────────────────

export interface SubmitRequestBody {
  client_id: string
  requirement: TaskRequirement
}

export async function listRequests(
  params?: PaginationParams & { status?: RequestStatus },
): Promise<PaginatedResult<ClientRequest>> {
  const response = await apiClient.get<PaginatedResponse<ClientRequest>>(
    '/requests',
    { params },
  )
  return unwrapPaginated<ClientRequest>(response)
}

export async function getRequest(requestId: string): Promise<ClientRequest> {
  const response = await apiClient.get<ApiResponse<ClientRequest>>(
    `/requests/${encodeURIComponent(requestId)}`,
  )
  return unwrap(response)
}

export async function submitRequest(
  data: SubmitRequestBody,
): Promise<ClientRequest> {
  const response = await apiClient.post<ApiResponse<ClientRequest>>(
    '/requests/',
    data,
  )
  return unwrap(response)
}

export async function approveRequest(requestId: string): Promise<ClientRequest> {
  const response = await apiClient.post<ApiResponse<ClientRequest>>(
    `/requests/${encodeURIComponent(requestId)}/approve`,
  )
  return unwrap(response)
}

export async function rejectRequest(
  requestId: string,
  reason: string,
): Promise<ClientRequest> {
  const response = await apiClient.post<ApiResponse<ClientRequest>>(
    `/requests/${encodeURIComponent(requestId)}/reject`,
    { reason },
  )
  return unwrap(response)
}

export interface ScopeRequestBody {
  notes: string
  refined_title?: string
  refined_description?: string
  refined_acceptance_criteria?: readonly string[]
}

export async function scopeRequest(
  requestId: string,
  data: ScopeRequestBody,
): Promise<ClientRequest> {
  const response = await apiClient.post<ApiResponse<ClientRequest>>(
    `/requests/${encodeURIComponent(requestId)}/scope`,
    data,
  )
  return unwrap(response)
}

// ── Simulations ─────────────────────────────────────────────────

export async function listSimulations(
  params?: PaginationParams,
): Promise<PaginatedResult<SimulationStatus>> {
  const response = await apiClient.get<PaginatedResponse<SimulationStatus>>(
    '/simulations',
    { params },
  )
  return unwrapPaginated<SimulationStatus>(response)
}

export async function getSimulation(
  simulationId: string,
): Promise<SimulationStatus> {
  const response = await apiClient.get<ApiResponse<SimulationStatus>>(
    `/simulations/${encodeURIComponent(simulationId)}`,
  )
  return unwrap(response)
}

export async function startSimulation(
  config: SimulationConfig,
): Promise<SimulationStatus> {
  try {
    const response = await apiClient.post<ApiResponse<SimulationStatus>>(
      '/simulations/',
      { config },
    )
    return unwrap(response)
  } catch (err) {
    // The backend returns HTTP 409 when a simulation with
    // ``config.simulation_id`` is already registered (a redelivery
    // or 5xx-driven retry of the same request). Fall back to
    // fetching the existing run so the caller's retry path is
    // idempotent: retries observe the in-flight runner instead of
    // raising and forcing the user to refresh.
    if (axios.isAxiosError(err) && err.response?.status === 409) {
      return getSimulation(config.simulation_id)
    }
    throw err
  }
}

export async function cancelSimulation(
  simulationId: string,
): Promise<SimulationStatus> {
  const response = await apiClient.post<ApiResponse<SimulationStatus>>(
    `/simulations/${encodeURIComponent(simulationId)}/cancel`,
  )
  return unwrap(response)
}

export interface SimulationReport {
  format: string
  simulation_id: string
  status: string
  totals: Record<string, number>
  rates: Record<string, number>
  [key: string]: unknown
}

export async function getSimulationReport(
  simulationId: string,
  fmt: 'summary' | 'detailed' = 'summary',
): Promise<SimulationReport> {
  const response = await apiClient.get<ApiResponse<SimulationReport>>(
    `/simulations/${encodeURIComponent(simulationId)}/report`,
    { params: { fmt } },
  )
  return unwrap(response)
}

// ── Reviews ─────────────────────────────────────────────────────

export async function getReviewPipeline(
  taskId: string,
): Promise<PipelineResult> {
  const response = await apiClient.get<ApiResponse<PipelineResult>>(
    `/reviews/${encodeURIComponent(taskId)}/pipeline`,
  )
  return unwrap(response)
}

export type StageVerdict = 'pass' | 'fail' | 'skip'

export interface StageDecisionBody {
  verdict: StageVerdict
  reason?: string
}

export interface StageDecisionResult {
  task_id: string
  stage_name: string
  stage_result: ReviewStageResult
  pipeline_result: PipelineResult
}

export async function decideReviewStage(
  taskId: string,
  stageName: string,
  data: StageDecisionBody,
): Promise<StageDecisionResult> {
  // The backend treats `reason` as NotBlankStr | None: an empty or
  // whitespace-only string fails Pydantic validation. Trim and
  // coerce to undefined so callers can pass raw form state without
  // tripping a 422 at the API boundary.
  const trimmedReason = data.reason?.trim()
  const payload: StageDecisionBody = {
    verdict: data.verdict,
    ...(trimmedReason ? { reason: trimmedReason } : {}),
  }
  const response = await apiClient.post<ApiResponse<StageDecisionResult>>(
    `/reviews/${encodeURIComponent(taskId)}/stages/${encodeURIComponent(
      stageName,
    )}/decide`,
    payload,
  )
  return unwrap(response)
}

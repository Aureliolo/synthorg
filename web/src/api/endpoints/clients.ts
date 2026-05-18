import axios from 'axios'

import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ApiResponse, PaginatedResponse, PaginationParams } from '../types/http'
import type {
  ClientProfile,
  ClientRequest,
  CreateClientRequest,
  CreateRequestPayload,
  PipelineResult,
  RequestStatus,
  ReviewStageResult,
  SatisfactionHistory,
  ScopingPayload,
  SimulationConfig,
  SimulationStatusResponse,
  StageDecisionPayload,
  StageDecisionResult,
  UpdateClientRequest,
} from '@/api/types'

// DTO shapes are owned by the generated barrel (`@/api/types`,
// regenerated from the backend OpenAPI schema). Re-export the ones
// callers consume so the import site stays `@/api/endpoints/clients`
// without hand-maintaining the shapes here.
export type {
  ClientProfile,
  ClientRequest,
  CreateClientRequest,
  CreateRequestPayload,
  PipelineResult,
  RequestStatus,
  ReviewStageResult,
  SatisfactionHistory,
  SatisfactionPoint,
  ScopingPayload,
  SimulationConfig,
  SimulationMetrics,
  SimulationStatusResponse,
  StageDecisionPayload,
  StageDecisionResult,
  TaskRequirement,
  UpdateClientRequest,
} from '@/api/types'

// Derived from the generated stage result; not a hand-maintained
// duplicate (the verdict literal union has no standalone DTO).
export type StageVerdict = ReviewStageResult['verdict']

// The report endpoint returns a transport-shaped dict (no Pydantic
// DTO), so this view shape is intentionally local, not generated.
export interface SimulationReport {
  format: string
  simulation_id: string
  status: string
  totals: Record<string, number>
  rates: Record<string, number>
  [key: string]: unknown
}

// ── Clients ─────────────────────────────────────────────────────

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
  data: CreateClientRequest,
): Promise<ClientProfile> {
  const response = await apiClient.post<ApiResponse<ClientProfile>>(
    '/clients/',
    data,
  )
  return unwrap(response)
}

export async function updateClient(
  clientId: string,
  data: UpdateClientRequest,
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

export async function getClientSatisfaction(
  clientId: string,
): Promise<SatisfactionHistory> {
  const response = await apiClient.get<ApiResponse<SatisfactionHistory>>(
    `/clients/${encodeURIComponent(clientId)}/satisfaction`,
  )
  return unwrap(response)
}

// ── Requests ────────────────────────────────────────────────────

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
  data: CreateRequestPayload,
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

export async function scopeRequest(
  requestId: string,
  data: ScopingPayload,
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
): Promise<PaginatedResult<SimulationStatusResponse>> {
  const response = await apiClient.get<
    PaginatedResponse<SimulationStatusResponse>
  >('/simulations', { params })
  return unwrapPaginated<SimulationStatusResponse>(response)
}

export async function getSimulation(
  simulationId: string,
): Promise<SimulationStatusResponse> {
  const response = await apiClient.get<ApiResponse<SimulationStatusResponse>>(
    `/simulations/${encodeURIComponent(simulationId)}`,
  )
  return unwrap(response)
}

function configsEqual(a: SimulationConfig, b: SimulationConfig): boolean {
  // Field-by-field compare matches the actual ``SimulationConfig``
  // shape (five primitive fields) and stays correct under key-order
  // shifts that ``JSON.stringify`` would silently misreport.
  return (
    a.simulation_id === b.simulation_id &&
    a.project_id === b.project_id &&
    a.rounds === b.rounds &&
    a.clients_per_round === b.clients_per_round &&
    a.requirements_per_client === b.requirements_per_client
  )
}

export async function startSimulation(
  config: SimulationConfig,
): Promise<SimulationStatusResponse> {
  try {
    const response = await apiClient.post<
      ApiResponse<SimulationStatusResponse>
    >('/simulations/', { config })
    return unwrap(response)
  } catch (err) {
    // The backend returns HTTP 409 when a simulation with
    // ``config.simulation_id`` is already registered (a redelivery
    // or 5xx-driven retry of the same request). Make the retry path
    // idempotent only when the existing run was started with the
    // SAME config -- if the configs differ, the caller passed a
    // different request that happened to collide on
    // ``simulation_id`` and should see the 409 surface instead of
    // silently inheriting an unrelated in-flight runner.
    if (axios.isAxiosError(err) && err.response?.status === 409) {
      const existing = await getSimulation(config.simulation_id)
      if (configsEqual(existing.config, config)) {
        return existing
      }
    }
    throw err
  }
}

export async function cancelSimulation(
  simulationId: string,
): Promise<SimulationStatusResponse> {
  const response = await apiClient.post<ApiResponse<SimulationStatusResponse>>(
    `/simulations/${encodeURIComponent(simulationId)}/cancel`,
  )
  return unwrap(response)
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

export async function decideReviewStage(
  taskId: string,
  stageName: string,
  data: StageDecisionPayload,
): Promise<StageDecisionResult> {
  // The backend treats `reason` as NotBlankStr | None: an empty or
  // whitespace-only string fails Pydantic validation. Trim and
  // coerce to undefined so callers can pass raw form state without
  // tripping a 422 at the API boundary.
  const trimmedReason = data.reason?.trim()
  const payload: StageDecisionPayload = {
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

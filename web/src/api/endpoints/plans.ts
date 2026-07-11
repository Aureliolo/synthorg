import { apiClient, type PaginatedResult, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  EditPlanRequest,
  Plan,
  PlanFilters,
  RequestPlanChangesRequest,
} from '../types/plans'

/** List durable plans with optional status / project / objective filters. */
export async function listPlans(filters?: PlanFilters): Promise<PaginatedResult<Plan>> {
  const response = await apiClient.get<PaginatedResponse<Plan>>('/plans', {
    params: filters,
  })
  return unwrapPaginated<Plan>(response)
}

/** Fetch a single plan by id. */
export async function getPlan(planId: string): Promise<Plan> {
  const response = await apiClient.get<ApiResponse<Plan>>(
    `/plans/${encodeURIComponent(planId)}`,
  )
  return unwrap(response)
}

/** Rework a plan's items, producing a new revision under review. */
export async function editPlan(planId: string, data: EditPlanRequest): Promise<Plan> {
  const response = await apiClient.patch<ApiResponse<Plan>>(
    `/plans/${encodeURIComponent(planId)}`,
    data,
  )
  return unwrap(response)
}

/** Send a plan back to the org for revision, with a note. */
export async function requestPlanChanges(
  planId: string,
  data: RequestPlanChangesRequest,
): Promise<Plan> {
  const response = await apiClient.post<ApiResponse<Plan>>(
    `/plans/${encodeURIComponent(planId)}/request-changes`,
    data,
  )
  return unwrap(response)
}

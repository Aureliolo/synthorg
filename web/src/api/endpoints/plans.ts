import {
  apiClient,
  type PaginatedResult,
  unwrap,
  unwrapPaginated,
  unwrapVoid,
} from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type {
  EditPlanRequest,
  LifecycleTransition,
  Plan,
  PlanEvaluationResponse,
  PlanFilters,
  ReplanRequest,
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

/**
 * Fetch the evaluate stage's judgement history for a plan.
 *
 * Empty `attempts` means nothing has judged the objective yet, which is also
 * what an operator sees for a plan parked at EVALUATING because no verdict
 * ever landed. The plan's own status tells the two apart.
 */
export async function getPlanEvaluation(
  planId: string,
): Promise<PlanEvaluationResponse> {
  const response = await apiClient.get<ApiResponse<PlanEvaluationResponse>>(
    `/plans/${encodeURIComponent(planId)}/evaluation`,
  )
  return unwrap(response)
}

/**
 * Fetch the durable record of how a plan reached its current status.
 *
 * The status says where the plan is; these rows say how it got there and who
 * moved it, from persisted state rather than a container's log. Newest first.
 */
export async function getPlanTransitions(
  planId: string,
): Promise<readonly LifecycleTransition[]> {
  const response = await apiClient.get<ApiResponse<readonly LifecycleTransition[]>>(
    `/plans/${encodeURIComponent(planId)}/transitions`,
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

/**
 * Revise a dispatched plan, retiring it in favour of a successor.
 *
 * Distinct from `editPlan`: a dispatched plan's items are already building, so
 * they cannot be rewritten in place. Returns the successor, awaiting review.
 */
export async function replanPlan(planId: string, data: ReplanRequest): Promise<Plan> {
  const response = await apiClient.post<ApiResponse<Plan>>(
    `/plans/${encodeURIComponent(planId)}/replan`,
    data,
  )
  return unwrap(response)
}

/**
 * Remove a plan that never became work.
 *
 * The exit for a plan asking for a decision on work nothing can build: a shell
 * whose decomposition stranded, a draft, one waiting on review, or one that
 * failed. The API refuses a dispatched or decided plan, so the caller surfaces
 * that refusal rather than pre-judging it.
 */
export async function deletePlan(planId: string): Promise<void> {
  const response = await apiClient.delete<ApiResponse<null>>(
    `/plans/${encodeURIComponent(planId)}`,
  )
  unwrapVoid(response)
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

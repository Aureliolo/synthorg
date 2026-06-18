/**
 * A/B experiment registry endpoints.
 *
 * The variant and assignment lists are operator-facing reads. Variant
 * registration is surfaced in the dashboard via the experiment explorer's
 * form; ``/assign`` remains the runtime agent-only path.
 */
import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type { ExperimentAssignment, ExperimentVariant } from '../types'

const VARIANTS_PAGE_SIZE = 200

export interface RegisterVariantPayload {
  variant: string
  weight: number
  description?: string
}

/** List every registered variant for an experiment (all pages). */
export async function listVariants(
  experiment: string,
): Promise<readonly ExperimentVariant[]> {
  return paginateAll<ExperimentVariant>(async (cursor) => {
    const params: { limit: number; cursor?: string } = {
      limit: VARIANTS_PAGE_SIZE,
    }
    if (cursor) params.cursor = cursor
    const response = await apiClient.get<PaginatedResponse<ExperimentVariant>>(
      `/experiments/${encodeURIComponent(experiment)}/variants`,
      { params },
    )
    return unwrapPaginated<ExperimentVariant>(response)
  })
}

/** Register or replace a variant on an experiment. */
export async function registerVariant(
  experiment: string,
  payload: RegisterVariantPayload,
): Promise<ExperimentVariant> {
  const response = await apiClient.post<ApiResponse<ExperimentVariant>>(
    `/experiments/${encodeURIComponent(experiment)}/variants`,
    payload,
  )
  return unwrap(response)
}

/** List recorded assignments for an experiment (newest first). */
export async function listAssignments(
  experiment: string,
): Promise<readonly ExperimentAssignment[]> {
  return paginateAll<ExperimentAssignment>(async (cursor) => {
    const params: { limit: number; cursor?: string } = { limit: 200 }
    if (cursor) params.cursor = cursor
    const response = await apiClient.get<PaginatedResponse<ExperimentAssignment>>(
      `/experiments/${encodeURIComponent(experiment)}/assignments`,
      { params },
    )
    return unwrapPaginated<ExperimentAssignment>(response)
  })
}

/**
 * A/B experiment registry endpoints (read surface).
 *
 * The variant and assignment lists are operator-facing reads. The write
 * operations (``POST /experiments/{experiment}/variants`` and
 * ``/assign``) are intentionally backend-/agent-only and not surfaced in
 * the dashboard; see the experiments controller docstring.
 */
import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type { ExperimentAssignment, ExperimentVariant } from '../types'

/** List every registered variant for an experiment. */
export async function listVariants(
  experiment: string,
): Promise<readonly ExperimentVariant[]> {
  const response = await apiClient.get<ApiResponse<readonly ExperimentVariant[]>>(
    `/experiments/${encodeURIComponent(experiment)}/variants`,
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

import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type { PlanCommentPayload, PlanItemComment } from '../types/plans'

/** List a plan's comments (oldest first), optionally narrowed to one item. */
export async function listPlanComments(
  planId: string,
  itemId?: string,
): Promise<PlanItemComment[]> {
  const response = await apiClient.get<ApiResponse<PlanItemComment[]>>(
    `/plans/${encodeURIComponent(planId)}/comments`,
    { params: itemId === undefined ? undefined : { item_id: itemId } },
  )
  return unwrap(response)
}

/** Post a comment on a plan item; the author is the authenticated user. */
export async function addPlanComment(
  planId: string,
  itemId: string,
  data: PlanCommentPayload,
): Promise<PlanItemComment> {
  const response = await apiClient.post<ApiResponse<PlanItemComment>>(
    `/plans/${encodeURIComponent(planId)}/comments/items/${encodeURIComponent(itemId)}`,
    data,
  )
  return unwrap(response)
}

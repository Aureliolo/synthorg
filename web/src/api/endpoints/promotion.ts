import { apiClient, unwrap } from '../client'
import type {
  PromotionApplyResultDTO,
  PromotionEvaluationDTO,
  PromotionRecordDTO,
} from '../types'
import type { PromotionDirection } from '../types/enum-values.gen'
import type { ApiResponse } from '../types/http'

const BASE = '/promotion'

/** Evaluate an agent's eligibility for a promotion or demotion. */
export async function evaluatePromotion(
  agentId: string,
  direction: PromotionDirection,
): Promise<PromotionEvaluationDTO> {
  const response = await apiClient.get<ApiResponse<PromotionEvaluationDTO>>(
    `${BASE}/${encodeURIComponent(agentId)}/evaluate`,
    { params: { direction } },
  )
  return unwrap(response)
}

/** Full promotion / demotion record for a single agent. */
export async function getPromotionHistory(
  agentId: string,
): Promise<readonly PromotionRecordDTO[]> {
  const response = await apiClient.get<ApiResponse<readonly PromotionRecordDTO[]>>(
    `${BASE}/${encodeURIComponent(agentId)}/history`,
  )
  return unwrap(response)
}

/**
 * Request and conditionally apply a seniority change. The deciding operator is
 * taken from the authenticated session server-side; the client sends no actor
 * identity. Gated on the CEO / Manager role by the backend.
 */
export async function applyPromotion(
  agentId: string,
  direction: PromotionDirection,
): Promise<PromotionApplyResultDTO> {
  const response = await apiClient.post<ApiResponse<PromotionApplyResultDTO>>(
    `${BASE}/${encodeURIComponent(agentId)}/apply`,
    undefined,
    { params: { direction } },
  )
  return unwrap(response)
}

/** Run a full promotion-evaluation cycle over all active agents. */
export async function runPromotionCycle(): Promise<readonly PromotionRecordDTO[]> {
  const response = await apiClient.post<ApiResponse<readonly PromotionRecordDTO[]>>(
    `${BASE}/cycle`,
  )
  return unwrap(response)
}

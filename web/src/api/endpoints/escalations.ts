/**
 * Human escalation approval queue client.
 *
 * Operators list pending escalations, inspect the originating conflict,
 * submit a decision (winner or reject), or cancel a stuck escalation.
 * Responses flow through the shared {@link ApiResponse} /
 * {@link PaginatedResponse} envelopes; 429 responses are handled
 * transparently by the shared axios client (see ``api/client.ts``).
 */

import {
  apiClient,
  unwrap,
  unwrapPaginated,
  type PaginatedResult,
} from '../client'
import type {
  CancelEscalationRequest,
  EscalationResponse,
  EscalationStatus,
  SubmitDecisionRequest,
} from '../types/escalations'
import type { ApiResponse, PaginatedResponse } from '../types/http'

const BASE = '/conflicts/escalations'

export interface ListEscalationsFilters {
  readonly status?: EscalationStatus
  readonly limit?: number
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  readonly cursor?: string | null
}

export async function listEscalations(
  filters?: ListEscalationsFilters,
): Promise<PaginatedResult<EscalationResponse>> {
  const response = await apiClient.get<PaginatedResponse<EscalationResponse>>(
    BASE,
    { params: filters },
  )
  return unwrapPaginated<EscalationResponse>(response)
}

export async function getEscalation(id: string): Promise<EscalationResponse> {
  const response = await apiClient.get<ApiResponse<EscalationResponse>>(
    `${BASE}/${encodeURIComponent(id)}`,
  )
  return unwrap(response)
}

export async function submitEscalationDecision(
  id: string,
  data: SubmitDecisionRequest,
): Promise<EscalationResponse> {
  const response = await apiClient.post<ApiResponse<EscalationResponse>>(
    `${BASE}/${encodeURIComponent(id)}/decision`,
    data,
  )
  return unwrap(response)
}

export async function cancelEscalation(
  id: string,
  data: CancelEscalationRequest,
): Promise<EscalationResponse> {
  const response = await apiClient.post<ApiResponse<EscalationResponse>>(
    `${BASE}/${encodeURIComponent(id)}/cancel`,
    data,
  )
  return unwrap(response)
}

/**
 * SSRF-violation review-queue client.
 *
 * Operators list outbound URLs the provider egress guard blocked, then
 * allow or deny each pending violation. List responses flow through the
 * shared {@link PaginatedResponse} envelope; the resolve mutation returns a
 * single {@link ApiResponse}. 429s are handled by the shared axios client.
 */

import { apiClient, unwrap, unwrapPaginated, type PaginatedResult } from '../client'
import type { ResolveSsrfViolationRequest, SsrfViolationDTO } from '../types'
import type { SsrfViolationStatus } from '../types/enum-values.gen'
import type { ApiResponse, PaginatedResponse } from '../types/http'

const BASE = '/providers/ssrf-violations'

export interface ListSsrfViolationsFilters {
  readonly status?: SsrfViolationStatus
  readonly limit?: number
  /** Opaque pagination cursor from the previous response's `pagination.next_cursor`. */
  readonly cursor?: string | null
}

export async function listSsrfViolations(
  filters?: ListSsrfViolationsFilters,
): Promise<PaginatedResult<SsrfViolationDTO>> {
  const response = await apiClient.get<PaginatedResponse<SsrfViolationDTO>>(`${BASE}/`, {
    params: filters,
  })
  return unwrapPaginated<SsrfViolationDTO>(response)
}

/** Allow or deny a pending violation. Gated on the CEO / Manager role server-side. */
export async function resolveSsrfViolation(
  id: string,
  status: ResolveSsrfViolationRequest['status'],
): Promise<SsrfViolationDTO> {
  const response = await apiClient.post<ApiResponse<SsrfViolationDTO>>(
    `${BASE}/${encodeURIComponent(id)}/resolve`,
    { status } satisfies ResolveSsrfViolationRequest,
  )
  return unwrap(response)
}

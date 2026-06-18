/**
 * Interrupt polling-fallback endpoints.
 *
 * Used when the SSE / WebSocket transport is unavailable: lists pending
 * interrupts and resumes them via the REST path that mirrors the live
 * steering controls.
 */
import { apiClient, paginateAll, unwrap, unwrapPaginated } from '../client'
import type { ApiResponse, PaginatedResponse } from '../types/http'
import type { InterruptResponse, ResumeInterruptRequest } from '../types'

const INTERRUPTS_PAGE_SIZE = 200

/** List pending interrupts, optionally scoped to a session (all pages). */
export async function listInterrupts(
  sessionId?: string,
): Promise<readonly InterruptResponse[]> {
  return paginateAll<InterruptResponse>(async (cursor) => {
    const params: { limit: number; cursor?: string; session_id?: string } = {
      limit: INTERRUPTS_PAGE_SIZE,
    }
    if (cursor) params.cursor = cursor
    if (sessionId) params.session_id = sessionId
    const response = await apiClient.get<PaginatedResponse<InterruptResponse>>(
      '/interrupts',
      { params },
    )
    return unwrapPaginated<InterruptResponse>(response)
  })
}

/** Resume a pending interrupt (approve / reject / clarify). */
export async function resumeInterrupt(
  interruptId: string,
  data: ResumeInterruptRequest,
): Promise<Record<string, string>> {
  const response = await apiClient.post<ApiResponse<Record<string, string>>>(
    `/interrupts/${encodeURIComponent(interruptId)}/resume`,
    data,
  )
  return unwrap(response)
}

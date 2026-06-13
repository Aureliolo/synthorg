/**
 * Interrupt polling-fallback endpoints.
 *
 * Used when the SSE / WebSocket transport is unavailable: lists pending
 * interrupts and resumes them via the REST path that mirrors the live
 * steering controls.
 */
import { apiClient, unwrap } from '../client'
import type { ApiResponse } from '../types/http'
import type { InterruptResponse, ResumeInterruptRequest } from '../types'

/** List pending interrupts, optionally scoped to a session. */
export async function listInterrupts(
  sessionId?: string,
): Promise<readonly InterruptResponse[]> {
  const params = sessionId ? { session_id: sessionId } : undefined
  const response = await apiClient.get<ApiResponse<readonly InterruptResponse[]>>(
    '/interrupts',
    { params },
  )
  return unwrap(response)
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

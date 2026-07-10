/** Approval, coordination, and meeting WebSocket payload interfaces. */

import type { ApprovalResponse } from '@/api/types/approvals'

/**
 * Approval lifecycle WS payload. Carries `approval_id` / `status` at the top
 * level for cheap envelope routing plus the full enriched `approval` (resolved
 * task/project/agent names + run outcome) that the store upserts, so a
 * decision or expiry reflects in the queue live. Mirrors `_ApprovalEventBase`
 * in `api/ws_payloads/_lifecycle.py`.
 */
export interface WsApprovalEventPayload {
  approval_id: string
  status: string
  approval: ApprovalResponse
}

export interface WsCoordinationStartedPayload {
  task_id: string
  agent_count: number
}

export interface WsCoordinationPhaseCompletedPayload {
  task_id: string
  phase: string
  success: boolean
  duration_seconds?: number | null
}

export interface WsCoordinationCompletedPayload {
  task_id: string
  topology: string
  is_success: boolean
  total_duration_seconds: number
}

export interface WsCoordinationFailedPayload {
  task_id: string
  phase?: string | null
  topology?: string | null
  is_success?: boolean | null
  total_duration_seconds?: number | null
  error?: string | null
}

export interface WsMeetingStartedPayload {
  meeting_id: string
  meeting_type: string
  project_id?: string | null
  department?: string | null
  readonly participants: readonly string[]
}

export interface WsMeetingCompletedPayload extends WsMeetingStartedPayload {
  duration_seconds?: number | null
  summary?: string | null
}

export interface WsMeetingFailedPayload extends WsMeetingStartedPayload {
  error?: string | null
  reason?: string | null
}

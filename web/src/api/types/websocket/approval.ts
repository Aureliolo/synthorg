/** Approval, coordination, and meeting WebSocket payload interfaces. */

export interface WsApprovalEventPayload {
  approval_id: string
  status: string
  action_type: string
  risk_level: string
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

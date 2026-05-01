/** Conflict resolution & human escalation queue types. */

import type { SeniorityLevel } from './enums'

export type ConflictType =
  | 'resource'
  | 'authority'
  | 'process'
  | 'strategy'
  | 'technical'
  | 'architecture'

export type ConflictResolutionOutcome =
  | 'resolved_by_authority'
  | 'resolved_by_debate'
  | 'resolved_by_hybrid'
  | 'resolved_by_human'
  | 'rejected_by_human'
  | 'escalated_to_human'

export type EscalationStatus = 'pending' | 'decided' | 'expired' | 'cancelled'

export interface ConflictPosition {
  readonly agent_id: string
  readonly agent_department: string
  readonly agent_level: SeniorityLevel
  readonly position: string
  readonly reasoning: string
  readonly timestamp: string
}

export interface Conflict {
  readonly id: string
  readonly type: ConflictType
  readonly task_id: string | null
  readonly subject: string
  readonly positions: readonly ConflictPosition[]
  readonly detected_at: string
  readonly is_cross_department?: boolean
}

export interface WinnerDecision {
  readonly type: 'winner'
  readonly winning_agent_id: string
  readonly reasoning: string
}

export interface RejectDecision {
  readonly type: 'reject'
  readonly reasoning: string
}

export type EscalationDecision = WinnerDecision | RejectDecision

export interface Escalation {
  readonly id: string
  readonly conflict: Conflict
  readonly status: EscalationStatus
  readonly created_at: string
  readonly expires_at: string | null
  readonly decided_at: string | null
  readonly decided_by: string | null
  readonly decision: EscalationDecision | null
}

export interface EscalationResponse {
  readonly escalation: Escalation
  readonly conflict_id: string
  readonly status: EscalationStatus
}

export interface SubmitDecisionRequest {
  readonly decision: EscalationDecision
}

export interface CancelEscalationRequest {
  readonly reason: string
}

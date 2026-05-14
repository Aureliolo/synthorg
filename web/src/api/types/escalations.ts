/** Conflict resolution and human escalation queue types. */

export type {
  CancelEscalationRequest,
  Conflict,
  ConflictPosition,
  Escalation,
  EscalationResponse,
  RejectDecision,
  SubmitDecisionRequest,
  WinnerDecision,
} from './dtos.gen'

export type { ConflictType, EscalationStatus } from './enum-values.gen'
export {
  CONFLICT_TYPE_VALUES,
  ESCALATION_STATUS_VALUES,
} from './enum-values.gen'

import type { RejectDecision, WinnerDecision } from './dtos.gen'

/** Discriminated union of decision payloads (the wire surfaces the
 *  variants as ``Conflict.resolution`` and ``Escalation.decision``;
 *  OpenAPI emits them as separate schemas without a named union). */
export type EscalationDecision = WinnerDecision | RejectDecision

/** Frontend-only conflict-resolution outcome string union (mirrors a
 *  Pydantic Literal that is referenced via a non-DTO chain so the
 *  OpenAPI schema does not expose it as a named enum). */
export type ConflictResolutionOutcome =
  | 'resolved_by_authority'
  | 'resolved_by_debate'
  | 'resolved_by_hybrid'
  | 'resolved_by_human'
  | 'rejected_by_human'
  | 'escalated_to_human'

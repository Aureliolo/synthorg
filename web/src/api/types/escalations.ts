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

import type { RejectDecision, WinnerDecision } from './dtos.gen'

/** Discriminated union of decision payloads (the wire surfaces the
 *  variants as ``Conflict.resolution`` and ``Escalation.decision``;
 *  OpenAPI emits them as separate schemas without a named union). */
export type EscalationDecision = WinnerDecision | RejectDecision

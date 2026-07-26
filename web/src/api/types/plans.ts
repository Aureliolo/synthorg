/** Plan-review domain types: DTO re-exports plus frontend-only filters. */

import type { PlanStatus } from './enum-values.gen'

export type { PlanStatus } from './enum-values.gen'
export type {
  EditPlanRequest,
  Plan,
  PlanCommentPayload,
  PlanItem,
  PlanItemComment,
  PlanItemPayload,
  PlanOption,
  PlanReview,
  PlanReviewerVerdict,
  PlanReviewFinding,
  PlanVersionSnapshot,
  ReplanRequest,
  RequestPlanChangesRequest,
  SubmitObjectiveAck,
  SubmitObjectivePayload,
} from './dtos.gen'

/** Query filters for the plan list endpoint (all optional). */
export interface PlanFilters {
  readonly status?: PlanStatus
  readonly project?: string
  readonly objective_id?: string
  readonly cursor?: string | null
  readonly limit?: number
}

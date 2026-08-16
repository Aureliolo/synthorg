/** Plan-review domain types: DTO re-exports plus frontend-only filters. */

import type { PlanStatus } from './enum-values.gen'

export type { CriterionOutcome, PlanStatus } from './enum-values.gen'
export {
  PLAN_REVIEW_FINDING_CATEGORY_VALUES,
  PLAN_STATUS_VALUES,
} from './enum-values.gen'
// The ROW is what the endpoints return: the plan, and every item's owner
// already resolved to a name. The dashboard has no other plan shape.
export type { PlanItemRow as PlanItem, PlanRow as Plan } from './dtos.gen'
export type {
  CriterionVerdict,
  EditPlanRequest,
  LifecycleTransition,
  PlanCommentPayload,
  PlanEvaluationAttempt,
  PlanEvaluationResponse,
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

/** Plan-review domain types: DTO re-exports plus frontend-only filters. */

import type { PlanStatus } from './enum-values.gen'

export type { PlanStatus } from './enum-values.gen'
export type {
  EditPlanRequest,
  Plan,
  PlanItem,
  PlanItemPayload,
  RequestPlanChangesRequest,
} from './dtos.gen'

/** Query filters for the plan list endpoint (all optional). */
export interface PlanFilters {
  readonly status?: PlanStatus
  readonly project?: string
  readonly objective_id?: string
  readonly cursor?: string | null
  readonly limit?: number
}

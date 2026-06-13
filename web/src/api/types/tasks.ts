/** Task domain types. */

import type { Task as WireTask } from './dtos.gen'
import type { TaskSource, TaskStatus } from './enums'

export type {
  AcceptanceCriterion,
  CancelTaskRequest,
  CreateTaskRequest,
  ExpectedArtifact,
  TaskBoardSubmissionResponse,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from './dtos.gen'

/**
 * Task with optional frontend / WS-augmented extras the wire ``Task``
 * schema does not carry. ``cost``, ``version``, ``created_at`` and
 * ``updated_at`` arrive on the WS task-updated payload and on
 * dashboard-augmented projections; the wire HTTP responses do NOT
 * include them. ``source`` IS on the wire (required-but-nullable
 * after the codegen post-process) and re-stated here for symmetry.
 *
 * The wire's required-vs-optional shape is now correct out of the
 * generator (every Pydantic field is promoted to ``required[]`` on
 * response-side schemas; see ``scripts/generate_dto_types_ts.py``),
 * so this type only ADDS optional extras: it is NOT an
 * ``Omit<Wire, ...> & { ... }`` tightening overlay.
 */
export type Task = WireTask & {
  readonly source?: TaskSource | null | undefined
  readonly cost?: number | undefined
  readonly version?: number | undefined
  readonly created_at?: string | undefined
  readonly updated_at?: string | undefined
}

/**
 * Alias retained for call sites that explicitly mark the dashboard
 * view (vs. the strict wire shape from ``dtos.gen``). New code should
 * prefer ``Task`` directly; the alias keeps the boundary names
 * available for places that want to be explicit.
 */
export type DashboardTask = Task

/** Frontend-only query filter (not a Pydantic DTO). */
export interface TaskFilters {
  status?: TaskStatus
  assigned_to?: string
  project?: string
  limit?: number
  cursor?: string | null
}

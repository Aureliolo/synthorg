/** Task domain types. */

import type { Task as WireTask } from './dtos.gen'
import type {
  Complexity,
  CoordinationTopology,
  Priority,
  TaskSource,
  TaskStatus,
  TaskStructure,
  TaskType,
} from './enums'

export type {
  AcceptanceCriterion,
  CancelTaskRequest,
  CreateTaskRequest,
  ExpectedArtifact,
  TransitionTaskRequest,
  UpdateTaskRequest,
} from './dtos.gen'

/**
 * Task with the Pydantic-defaulted fields re-typed as required (the
 * wire serialiser emits them on every response) plus a handful of
 * runtime / WS-only fields the dashboard relies on (``cost``,
 * ``version``, ``created_at``, ``updated_at`` arrive on the WS
 * task-updated payload even though the HTTP list / get endpoints
 * project a lighter shape).
 */
export type Task = Omit<
  WireTask,
  | 'acceptance_criteria'
  | 'artifacts_expected'
  | 'budget_limit'
  | 'coordination_topology'
  | 'delegation_chain'
  | 'dependencies'
  | 'estimated_complexity'
  | 'max_retries'
  | 'priority'
  | 'reviewers'
  | 'status'
  | 'task_structure'
  | 'type'
> & {
  readonly status: TaskStatus
  readonly priority: Priority
  readonly type: TaskType
  readonly estimated_complexity: Complexity
  readonly coordination_topology: CoordinationTopology
  readonly task_structure: TaskStructure | null
  readonly budget_limit: number
  readonly max_retries: number
  readonly reviewers: readonly string[]
  readonly dependencies: readonly string[]
  readonly delegation_chain: readonly string[]
  readonly artifacts_expected: readonly import('./dtos.gen').ExpectedArtifact[]
  readonly acceptance_criteria: readonly import('./dtos.gen').AcceptanceCriterion[]
  readonly source?: TaskSource | null
  readonly cost?: number
  readonly version?: number
  readonly created_at?: string
  readonly updated_at?: string
}

/** Frontend-only query filter (not a Pydantic DTO). */
export interface TaskFilters {
  status?: TaskStatus
  assigned_to?: string
  project?: string
  limit?: number
  cursor?: string
}

/** Re-exports of generated enum tuples + frontend-side helpers.
 *
 * The runtime ``*_VALUES`` tuples and their derived string-union
 * types live in ``enum-values.gen.ts`` (regenerated from the
 * OpenAPI schema by ``scripts/generate_dto_types_ts.py``). This
 * file is the stable import surface for dashboard consumers and
 * the home of any frontend-only helpers (type guards, sets, etc.)
 * that compose with the generated tuples.
 *
 * The re-export block below lists what the dashboard actually
 * imports, not everything the generator emits: an entry no module
 * consumes is surface nobody asked for, and knip's ``types`` report
 * fails the build on one. Adding a new enum to the wire contract:
 * land it on the Pydantic side, regenerate, then surface it here in
 * the same commit as the consumer that needs it. Frontend-only enums
 * (UI state, never seen by the backend) belong further down, in the
 * "Frontend-only enums" section.
 */

export {
  AGENT_STATUS_VALUES,
  APPROVAL_RISK_LEVEL_VALUES,
  APPROVAL_SOURCE_VALUES,
  APPROVAL_STATUS_VALUES,
  ARTIFACT_TYPE_VALUES,
  AUTONOMY_LEVEL_VALUES,
  COMPLEXITY_VALUES,
  COORDINATION_TOPOLOGY_VALUES,
  DEPARTMENT_NAME_VALUES,
  PRIORITY_VALUES,
  PROJECT_STATUS_VALUES,
  RUN_OUTCOME_VALUES,
  STAKES_VALUES,
  TASK_SOURCE_VALUES,
  TASK_STATUS_VALUES,
  TASK_STRUCTURE_VALUES,
  TASK_TYPE_VALUES,
  URGENCY_LEVEL_VALUES,
  type AgentStatus,
  type ApprovalRiskLevel,
  type ApprovalSource,
  type ApprovalStatus,
  type ArtifactType,
  type AutonomyLevel,
  type Complexity,
  type DepartmentName,
  type HumanRole,
  type OrgRole,
  type Priority,
  type ProjectStatus,
  type RunOutcome,
  type Stakes,
  type TaskSource,
  type TaskStatus,
  type TaskStructure,
  type TaskType,
  type UrgencyLevel,
} from './enum-values.gen'

import { DEPARTMENT_NAME_VALUES, type DepartmentName } from './enum-values.gen'

const DEPARTMENT_NAME_SET: ReadonlySet<string> = new Set(DEPARTMENT_NAME_VALUES)

/**
 * Type guard for {@link DepartmentName}. Lets callers narrow a raw
 * string (e.g. from a select element's value) to the strict union
 * without a cast.
 */
export function isDepartmentName(value: string): value is DepartmentName {
  return DEPARTMENT_NAME_SET.has(value)
}

/** Re-exports of generated enum tuples + frontend-side helpers.
 *
 * The runtime ``*_VALUES`` tuples and their derived string-union
 * types live in ``enum-values.gen.ts`` (regenerated from the
 * OpenAPI schema by ``scripts/generate_dto_types_ts.py``). This
 * file is the stable import surface for dashboard consumers and
 * the home of any frontend-only helpers (type guards, sets, etc.)
 * that compose with the generated tuples.
 *
 * Adding a new enum to the wire contract: land it on the Pydantic
 * side, regenerate, then surface it here in the re-export block
 * below. Frontend-only enums (UI state, never seen by the
 * backend) belong further down, in the "Frontend-only enums"
 * section.
 */

export {
  ACTIVITY_EVENT_TYPE_VALUES,
  AGENT_STATUS_VALUES,
  APPROVAL_RISK_LEVEL_VALUES,
  APPROVAL_SOURCE_VALUES,
  APPROVAL_STATUS_VALUES,
  ARTIFACT_TYPE_VALUES,
  AUTONOMY_LEVEL_VALUES,
  COLLABORATION_PREFERENCE_VALUES,
  COMMUNICATION_VERBOSITY_VALUES,
  COMPLEXITY_VALUES,
  CONFLICT_APPROACH_VALUES,
  COORDINATION_TOPOLOGY_VALUES,
  CREATIVITY_LEVEL_VALUES,
  DECISION_MAKING_STYLE_VALUES,
  DEPARTMENT_NAME_VALUES,
  HUMAN_ROLE_VALUES,
  MEMORY_LEVEL_VALUES,
  ORG_ROLE_VALUES,
  PRIORITY_VALUES,
  PROJECT_STATUS_VALUES,
  RISK_TOLERANCE_VALUES,
  SENIORITY_LEVEL_VALUES,
  TASK_SOURCE_VALUES,
  TASK_STATUS_VALUES,
  TASK_STRUCTURE_VALUES,
  TASK_TYPE_VALUES,
  TOOL_ACCESS_LEVEL_VALUES,
  URGENCY_LEVEL_VALUES,
  type ActivityEventType,
  type AgentStatus,
  type ApprovalRiskLevel,
  type ApprovalStatus,
  type ArtifactType,
  type AutonomyLevel,
  type CollaborationPreference,
  type CommunicationVerbosity,
  type Complexity,
  type ConflictApproach,
  type CoordinationTopology,
  type CreativityLevel,
  type DecisionMakingStyle,
  type DepartmentName,
  type HumanRole,
  type MemoryLevel,
  type OrgRole,
  type Priority,
  type ProjectStatus,
  type RiskTolerance,
  type SeniorityLevel,
  type TaskSource,
  type TaskStatus,
  type TaskStructure,
  type TaskType,
  type ToolAccessLevel,
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

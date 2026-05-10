/** Enum types and their runtime VALUES arrays shared across the dashboard.
 *
 * Each runtime ``*_VALUES`` tuple is the single source of truth; the
 * matching type is derived as ``(typeof FOO_VALUES)[number]`` so type
 * and value stay in lockstep with one edit.  The shapes mirror the
 * Pydantic ``StrEnum`` / ``Literal`` types in the Python backend; new
 * enum members must be added to both sides together.
 */

export const SENIORITY_LEVEL_VALUES = [
  'junior', 'mid', 'senior', 'lead', 'principal', 'director', 'vp', 'c_suite',
] as const
export type SeniorityLevel = (typeof SENIORITY_LEVEL_VALUES)[number]

export const AGENT_STATUS_VALUES = [
  'active', 'onboarding', 'on_leave', 'terminated',
] as const
export type AgentStatus = (typeof AGENT_STATUS_VALUES)[number]

export const TASK_STATUS_VALUES = [
  'created', 'assigned', 'in_progress', 'in_review', 'completed',
  'blocked', 'failed', 'interrupted', 'suspended', 'cancelled',
  'rejected', 'auth_required',
] as const
export type TaskStatus = (typeof TASK_STATUS_VALUES)[number]

export const TASK_TYPE_VALUES = [
  'development', 'design', 'research', 'review', 'meeting', 'admin',
] as const
export type TaskType = (typeof TASK_TYPE_VALUES)[number]

export const PRIORITY_VALUES = [
  'critical', 'high', 'medium', 'low',
] as const
export type Priority = (typeof PRIORITY_VALUES)[number]

export const APPROVAL_STATUS_VALUES = [
  'pending', 'approved', 'rejected', 'expired',
] as const
export type ApprovalStatus = (typeof APPROVAL_STATUS_VALUES)[number]

export const APPROVAL_RISK_LEVEL_VALUES = [
  'low', 'medium', 'high', 'critical',
] as const
export type ApprovalRiskLevel = (typeof APPROVAL_RISK_LEVEL_VALUES)[number]

export const URGENCY_LEVEL_VALUES = [
  'critical', 'high', 'normal', 'no_expiry',
] as const
export type UrgencyLevel = (typeof URGENCY_LEVEL_VALUES)[number]

export const TASK_SOURCE_VALUES = [
  'internal', 'client', 'simulation',
] as const
export type TaskSource = (typeof TASK_SOURCE_VALUES)[number]

export const DEPARTMENT_NAME_VALUES = [
  'executive', 'product', 'design', 'engineering', 'quality_assurance',
  'data_analytics', 'operations', 'creative_marketing', 'security',
] as const
export type DepartmentName = (typeof DEPARTMENT_NAME_VALUES)[number]

const DEPARTMENT_NAME_SET: ReadonlySet<string> = new Set(DEPARTMENT_NAME_VALUES)

/**
 * Type guard for {@link DepartmentName}. Lets callers narrow a raw
 * string (e.g. from a select element's value) to the strict union
 * without a cast.
 */
export function isDepartmentName(value: string): value is DepartmentName {
  return DEPARTMENT_NAME_SET.has(value)
}

export const PROJECT_STATUS_VALUES = [
  'planning', 'active', 'on_hold', 'completed', 'cancelled',
] as const
export type ProjectStatus = (typeof PROJECT_STATUS_VALUES)[number]

export const ARTIFACT_TYPE_VALUES = [
  'code', 'tests', 'documentation',
] as const
export type ArtifactType = (typeof ARTIFACT_TYPE_VALUES)[number]

export const COMPLEXITY_VALUES = [
  'simple', 'medium', 'complex', 'epic',
] as const
export type Complexity = (typeof COMPLEXITY_VALUES)[number]

export const AUTONOMY_LEVEL_VALUES = [
  'full', 'semi', 'supervised', 'locked',
] as const
export type AutonomyLevel = (typeof AUTONOMY_LEVEL_VALUES)[number]

export const ORG_ROLE_VALUES = [
  'owner', 'department_admin', 'editor', 'viewer',
] as const
export type OrgRole = (typeof ORG_ROLE_VALUES)[number]

export const HUMAN_ROLE_VALUES = [
  'ceo', 'manager', 'board_member', 'pair_programmer', 'observer', 'system',
] as const
export type HumanRole = (typeof HUMAN_ROLE_VALUES)[number]

// The following types are not yet exposed in the OpenAPI surface;
// they remain hand-maintained until the backend enums land in the
// schema.
export type RiskTolerance = 'low' | 'medium' | 'high'
export type CreativityLevel = 'low' | 'medium' | 'high'
export type DecisionMakingStyle = 'analytical' | 'intuitive' | 'consultative' | 'directive'
export type CollaborationPreference = 'independent' | 'pair' | 'team'
export type CommunicationVerbosity = 'terse' | 'balanced' | 'verbose'
export type ConflictApproach = 'avoid' | 'accommodate' | 'compete' | 'compromise' | 'collaborate'
export type TaskStructure = 'sequential' | 'parallel' | 'mixed'
export type CoordinationTopology = 'sas' | 'centralized' | 'decentralized' | 'context_dependent' | 'auto'
export type ToolAccessLevel = 'sandboxed' | 'restricted' | 'standard' | 'elevated' | 'custom'
export type MemoryLevel = 'persistent' | 'project' | 'session' | 'none'

/** Enum types and their runtime VALUES arrays shared across the dashboard.
 *
 * Type definitions are re-exported from ``generated.d.ts`` (produced by
 * ``openapi-typescript`` against ``docs/openapi/openapi.json``) so the
 * shapes track the backend schema automatically.  The runtime ``*_VALUES``
 * arrays are hand-maintained because OpenAPI does not emit runtime
 * iterables -- new enum members must be added to both the schema and
 * the matching ``_VALUES`` array below.
 */

import type { components } from './generated'

type Schemas = components['schemas']

export type TaskStatus = Schemas['TaskStatus']
export type TaskType = Schemas['TaskType']
export type TaskSource = Schemas['TaskSource']
export type Priority = Schemas['Priority']
export type Complexity = Schemas['Complexity']
export type ApprovalStatus = Schemas['ApprovalStatus']
export type ApprovalRiskLevel = Schemas['ApprovalRiskLevel']
export type UrgencyLevel = Schemas['UrgencyLevel']
export type SeniorityLevel = Schemas['SeniorityLevel']
export type AgentStatus = Schemas['AgentStatus']
export type AutonomyLevel = Schemas['AutonomyLevel']
export type OrgRole = Schemas['OrgRole']
export type HumanRole = Schemas['HumanRole']
export type DepartmentName = Schemas['DepartmentName']
export type ProjectStatus = Schemas['ProjectStatus']
export type ArtifactType = Schemas['ArtifactType']

export const SENIORITY_LEVEL_VALUES = [
  'junior', 'mid', 'senior', 'lead', 'principal', 'director', 'vp', 'c_suite',
] as const satisfies readonly SeniorityLevel[]

export const AGENT_STATUS_VALUES = [
  'active', 'onboarding', 'on_leave', 'terminated',
] as const satisfies readonly AgentStatus[]

export const TASK_STATUS_VALUES = [
  'created', 'assigned', 'in_progress', 'in_review', 'completed',
  'blocked', 'failed', 'interrupted', 'suspended', 'cancelled',
  'rejected', 'auth_required',
] as const satisfies readonly TaskStatus[]

export const TASK_TYPE_VALUES = [
  'development', 'design', 'research', 'review', 'meeting', 'admin',
] as const satisfies readonly TaskType[]

export const PRIORITY_VALUES = [
  'critical', 'high', 'medium', 'low',
] as const satisfies readonly Priority[]

export const APPROVAL_STATUS_VALUES = [
  'pending', 'approved', 'rejected', 'expired',
] as const satisfies readonly ApprovalStatus[]

export const APPROVAL_RISK_LEVEL_VALUES = [
  'low', 'medium', 'high', 'critical',
] as const satisfies readonly ApprovalRiskLevel[]

export const URGENCY_LEVEL_VALUES = [
  'critical', 'high', 'normal', 'no_expiry',
] as const satisfies readonly UrgencyLevel[]

export const TASK_SOURCE_VALUES = [
  'internal', 'client', 'simulation',
] as const satisfies readonly TaskSource[]

export const DEPARTMENT_NAME_VALUES = [
  'executive', 'product', 'design', 'engineering', 'quality_assurance',
  'data_analytics', 'operations', 'creative_marketing', 'security',
] as const satisfies readonly DepartmentName[]

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
] as const satisfies readonly ProjectStatus[]

export const ARTIFACT_TYPE_VALUES = [
  'code', 'tests', 'documentation',
] as const satisfies readonly ArtifactType[]

// The following types are not yet exposed in the OpenAPI surface;
// they remain hand-maintained until the backend enums land in the
// schema.  Moving them to the generated types is mechanical once
// the API models pick them up.
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

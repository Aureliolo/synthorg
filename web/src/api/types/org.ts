/** Company/organization structure, department and team mutation requests. */

import type { AgentConfig } from './agents'
import type { CeremonyPolicyConfig } from './ceremony-policy'
import type { AutonomyLevel, DepartmentName, SeniorityLevel } from './enums'

export interface Department {
  name: DepartmentName
  display_name?: string
  head?: string | null
  head_id?: string | null
  budget_percent?: number
  readonly teams: readonly TeamConfig[]
  autonomy_level?: AutonomyLevel | null
  ceremony_policy?: CeremonyPolicyConfig | null
  reporting_lines?: readonly DepartmentReportingLine[]
  policies?: Record<string, unknown>
}

export interface TeamConfig {
  name: string
  lead: string
  readonly members: readonly string[]
}

export interface DepartmentReportingLine {
  readonly subordinate: string
  readonly supervisor: string
  readonly subordinate_id?: string | null
  readonly supervisor_id?: string | null
}

export interface CompanyConfig {
  company_name: string
  autonomy_level?: AutonomyLevel
  budget_monthly?: number
  communication_pattern?: string
  readonly agents: readonly AgentConfig[]
  readonly departments: readonly Department[]
}

export interface UpdateCompanyRequest {
  company_name?: string
  autonomy_level?: AutonomyLevel
  budget_monthly?: number
  communication_pattern?: string
}

export interface CreateDepartmentRequest {
  name: string
  head?: string | null
  budget_percent?: number
  autonomy_level?: AutonomyLevel | null
}

/**
 * Request-specific team payload nested inside
 * {@link UpdateDepartmentRequest}.
 *
 * Distinct from the response-side {@link TeamConfig} so form/store
 * callers cannot accidentally send response-only fields. The backend
 * caps ``teams`` at {@link UPDATE_DEPARTMENT_MAX_TEAMS} entries;
 * validate length at the form/store boundary before issuing the
 * request rather than surfacing a server 422.
 */
export interface UpdateDepartmentTeam {
  name: string
  lead: string
  readonly members?: readonly string[]
}

/**
 * Matches ``UpdateDepartmentRequest.teams`` ``max_length=64`` bound on
 * ``synthorg.api.dto_org``. Exported so forms/stores validate before
 * sending rather than surfacing a server 422.
 */
export const UPDATE_DEPARTMENT_MAX_TEAMS = 64

export interface UpdateDepartmentRequest {
  head?: string | null
  budget_percent?: number
  autonomy_level?: AutonomyLevel | null
  teams?: readonly UpdateDepartmentTeam[]
  ceremony_policy?: CeremonyPolicyConfig | null
}

export interface ReorderDepartmentsRequest {
  readonly department_names: readonly string[]
}

export interface CreateTeamRequest {
  name: string
  lead: string
  members?: readonly string[]
}

export interface UpdateTeamRequest {
  name?: string
  lead?: string
  members?: readonly string[]
}

export interface ReorderTeamsRequest {
  readonly team_names: readonly string[]
}

/**
 * Optional pair of (provider, model id) used by agent mutation DTOs.
 * Either both fields are present as non-empty strings, or both are
 * omitted -- the backend validator rejects partial pairs with 422.
 * Expressed as a discriminated union so the TypeScript compiler flags
 * half-filled requests at the call site.
 */
export type AgentModelSelector =
  | { model_provider: string; model_id: string }
  | { model_provider?: undefined; model_id?: undefined }

/**
 * Create payload for an agent. Mirrors
 * `synthorg.api.dto_org.CreateAgentOrgRequest`.
 *
 * Backend validator requires `model_provider` and `model_id` to be
 * either both set or both omitted. See {@link AgentModelSelector}.
 */
export type CreateAgentOrgRequest = {
  name: string
  role: string
  department: DepartmentName
  level: SeniorityLevel
} & AgentModelSelector

/**
 * Partial update for an agent. Mirrors
 * `synthorg.api.dto_org.UpdateAgentOrgRequest`.
 *
 * Backend validator requires `model_provider` and `model_id` to be
 * either both set or both omitted. See {@link AgentModelSelector}.
 */
export type UpdateAgentOrgRequest = {
  name?: string
  role?: string
  department?: DepartmentName
  level?: SeniorityLevel
  autonomy_level?: AutonomyLevel | null
} & AgentModelSelector

export interface ReorderAgentsRequest {
  readonly agent_names: readonly string[]
}

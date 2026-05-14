/** Company/organization structure, department and team mutation requests. */

import type { AgentConfig } from './agents'
import type { AutonomyLevel, DepartmentName, SeniorityLevel } from './enums'

export type {
  CreateAgentOrgRequest,
  CreateDepartmentRequest,
  CreateTeamRequest,
  ReorderAgentsRequest,
  ReorderDepartmentsRequest,
  ReorderTeamsRequest,
  UpdateAgentOrgRequest,
  UpdateCompanyRequest,
  UpdateDepartmentRequest,
  UpdateTeamRequest,
} from './dtos.gen'

import type { Department as WireDepartment } from './dtos.gen'

/**
 * Department with the Pydantic-defaulted fields re-typed as optional
 * (the wire still serialises them as their defaults, but the dashboard
 * treats them as opt-in display fields and many test fixtures
 * construct minimal {name, teams} objects). The frontend-only
 * display_name is added for the synthetic-dept fall-through where no
 * backend department row exists for an agent's declared department.
 */
export type Department = Partial<Omit<WireDepartment, 'name' | 'teams'>> & {
  readonly name: string
  readonly teams: readonly {
    readonly name: string
    readonly lead: string
    readonly members: readonly string[]
  }[]
  readonly display_name?: string
}

/** Frontend-only shapes for embedded dict payloads (Pydantic
 *  validates them via ``model_validate`` on inline dicts; not
 *  surfaced as named OpenAPI components). */

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

/**
 * Request-specific team payload nested inside ``UpdateDepartmentRequest``.
 * The backend caps ``teams`` at {@link UPDATE_DEPARTMENT_MAX_TEAMS}
 * entries; validate length at the form/store boundary before issuing
 * the request rather than surfacing a server 422.
 */
export interface UpdateDepartmentTeam {
  name: string
  lead: string
  readonly members?: readonly string[]
}

/**
 * Matches ``UpdateDepartmentRequest.teams`` ``max_length=64`` bound in
 * ``synthorg.api.dto_org``. Exported so forms/stores validate before
 * sending rather than surfacing a server 422.
 */
export const UPDATE_DEPARTMENT_MAX_TEAMS = 64

/**
 * Optional pair of (provider, model id) used by agent mutation DTOs.
 * Either both fields are present as non-empty strings, or both are
 * omitted: the backend validator rejects partial pairs with 422.
 * Expressed as a discriminated union so the TypeScript compiler flags
 * half-filled requests at the call site.
 */
export type AgentModelSelector =
  | { model_provider: string; model_id: string }
  | { model_provider?: undefined; model_id?: undefined }

/**
 * Frontend aggregation of company + agents + departments used by the
 * org-edit views. The wire's ``CompanyConfig`` only carries top-level
 * policy fields (budget, autonomy, communication pattern); the
 * dashboard view combines that with the separately-fetched agent and
 * department lists.
 */
export interface CompanyConfig {
  company_name: string
  autonomy_level?: AutonomyLevel
  budget_monthly?: number
  communication_pattern?: string
  readonly agents: readonly AgentConfig[]
  readonly departments: readonly Department[]
}

/** Convenience type aliases used by older import paths. */
export type { AutonomyLevel, DepartmentName, SeniorityLevel }

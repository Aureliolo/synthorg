/** Company/organization structure, department and team mutation requests. */

import type { AgentConfig } from './agents'
import type { Department as WireDepartment } from './dtos.gen'
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

/**
 * Department with the dashboard-only ``display_name`` extra. The wire
 * does not carry it; the synthetic-dept fall-through (used when an
 * agent's declared department has no backend row) populates it from
 * the dept name.
 *
 * The wire's required-vs-optional shape is now correct out of the
 * generator, so this type only ADDS the optional ``display_name``:
 * it is NOT an ``Omit<Wire, ...> & { ... }`` tightening overlay.
 */
export type Department = WireDepartment & {
  readonly display_name?: string
}

/** Alias kept for call sites that want to be explicit. */
export type DashboardDepartment = Department

/** Frontend-only shapes for embedded dict payloads (Pydantic
 *  validates them via ``model_validate`` on inline dicts; not
 *  surfaced as named OpenAPI components). */

export interface TeamConfig {
  name: string
  lead: string
  readonly members: readonly string[]
}


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

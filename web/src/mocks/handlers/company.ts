import { http, HttpResponse } from 'msw'
import type {
  createAgentOrg,
  createDepartment,
  createTeam,
  getCompanyConfig,
  getDepartment,
  getDepartmentHealth,
  listDepartments,
  reorderAgents,
  reorderDepartments,
  reorderTeams,
  updateAgentOrg,
  updateCompany,
  updateDepartment,
  updateTeam,
} from '@/api/endpoints/company'
import type { AgentConfig } from '@/api/types/agents'
import type { DepartmentHealth } from '@/api/types/analytics'
import type { DepartmentName } from '@/api/types/enums'
import type { CompanyConfig, Department, TeamConfig } from '@/api/types/org'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'
import { buildAgent } from './agents'

function buildDepartment(
  overrides: Partial<Department> = {},
): Department {
  return {
    name: 'engineering',
    display_name: 'Engineering',
    head: null,
    head_id: null,
    budget_percent: 0,
    teams: [],
    autonomy_level: 'supervised',
    ceremony_policy: null,
    reporting_lines: [],
    policies: {
      approval_chains: [],
      review_requirements: {
        min_reviewers: 0,
        required_reviewer_roles: [],
        self_review_allowed: true,
      },
    },
    ...overrides,
  }
}

function buildCompanyConfig(
  overrides: Partial<CompanyConfig> = {},
): CompanyConfig {
  return {
    company_name: 'Default Company',
    autonomy_level: 'supervised',
    budget_monthly: 0,
    communication_pattern: 'hub_and_spoke',
    agents: [],
    departments: [],
    ...overrides,
  }
}

function buildDepartmentHealth(name: string): DepartmentHealth {
  return {
    department_name: name,
    agent_count: 0,
    active_agent_count: 0,
    currency: DEFAULT_CURRENCY,
    avg_performance_score: null,
    department_cost_7d: 0,
    cost_trend: [],
    collaboration_score: null,
    utilization_percent: 0,
  }
}

function buildTeam(overrides: Partial<TeamConfig> = {}): TeamConfig {
  return {
    name: 'default-team',
    lead: 'agent-default',
    members: [],
    ...overrides,
  }
}

export const companyHandlers = [
  http.get('/api/v1/company', () =>
    HttpResponse.json(successFor<typeof getCompanyConfig>(buildCompanyConfig())),
  ),
  http.patch('/api/v1/company', async ({ request }) => {
    const body = (await request.json()) as Partial<CompanyConfig>
    return HttpResponse.json(
      successFor<typeof updateCompany>(body),
    )
  }),
  http.get('/api/v1/departments', () =>
    HttpResponse.json(paginatedFor<typeof listDepartments>(emptyPage<Department>())),
  ),
  http.get('/api/v1/departments/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getDepartment>(
        buildDepartment({ name: String(params['name']) }),
      ),
    ),
  ),
  http.get('/api/v1/departments/:name/health', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getDepartmentHealth>(buildDepartmentHealth(String(params['name']))),
    ),
  ),
  http.post('/api/v1/departments', async ({ request }) => {
    const body = (await request.json()) as { name: string }
    return HttpResponse.json(
      successFor<typeof createDepartment>(
        buildDepartment({ name: body.name }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/departments/:name', async ({ params, request }) => {
    const body = (await request.json()) as Partial<Department>
    return HttpResponse.json(
      successFor<typeof updateDepartment>(
        buildDepartment({
          ...body,
          name: String(params['name']),
        }),
      ),
    )
  }),
  http.delete('/api/v1/departments/:name', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.post('/api/v1/company/reorder-departments', () =>
    HttpResponse.json(successFor<typeof reorderDepartments>([])),
  ),
  http.post('/api/v1/agents', async ({ request }) => {
    const body = (await request.json()) as {
      name: string
      role: string
      department: DepartmentName
    }
    return HttpResponse.json(
      successFor<typeof createAgentOrg>(
        buildAgent({
          name: body.name,
          role: body.role,
          department: body.department,
        }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/agents/:agentId', async ({ params, request }) => {
    const body = (await request.json()) as Partial<AgentConfig>
    return HttpResponse.json(
      successFor<typeof updateAgentOrg>(
        buildAgent({ ...body, id: String(params['agentId']) }),
      ),
    )
  }),
  http.delete('/api/v1/agents/:agentId', () => HttpResponse.json(voidSuccess())),
  http.post('/api/v1/departments/:name/reorder-agents', () =>
    HttpResponse.json(successFor<typeof reorderAgents>([])),
  ),
  http.post('/api/v1/departments/:name/teams', async ({ request }) => {
    const body = (await request.json()) as Partial<TeamConfig>
    return HttpResponse.json(
      successFor<typeof createTeam>(buildTeam({ ...body })),
      { status: 201 },
    )
  }),
  http.patch(
    '/api/v1/departments/:name/teams/:teamName',
    async ({ params, request }) => {
      const body = (await request.json()) as Partial<TeamConfig>
      return HttpResponse.json(
        successFor<typeof updateTeam>(
          buildTeam({ ...body, name: String(params['teamName']) }),
        ),
      )
    },
  ),
  http.delete('/api/v1/departments/:name/teams/:teamName', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.patch('/api/v1/departments/:name/teams/reorder', () =>
    HttpResponse.json(successFor<typeof reorderTeams>([])),
  ),
]

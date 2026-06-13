/**
 * Organisation mock-data builders (company config, departments, agents).
 *
 * Mirrors the wire shape of ``GET /api/v1/company`` (``CompanyConfig``)
 * so the org-edit flow specs can seed a deterministic company without a
 * live backend. The org-edit page reads ``config.departments`` (to
 * populate the agent-create department picker) and ``config.agents``
 * (to render the per-department agent board), so both are first-class
 * here. Field sets track the production ``CompanyConfig`` / ``Department``
 * / ``AgentConfig`` schemas (resolved against the OpenAPI spec); the
 * companion MSW builders in ``src/mocks/handlers/`` carry the same shape.
 */

export interface MockReviewRequirements {
  min_reviewers: number
  required_reviewer_roles: string[]
  self_review_allowed: boolean
}

export interface MockDepartmentPolicies {
  approval_chains: unknown[]
  review_requirements: MockReviewRequirements
}

export interface MockDepartment {
  name: string
  display_name: string
  head: string | null
  head_id: string | null
  budget_percent: number
  teams: unknown[]
  autonomy_level: 'supervised' | 'autonomous' | 'manual'
  ceremony_policy: null
  reporting_lines: unknown[]
  policies: MockDepartmentPolicies
}

export interface MockOrgAgent {
  id: string
  name: string
  role: string
  department: string
  level: 'junior' | 'mid' | 'senior' | 'principal'
  status: 'active' | 'idle' | 'paused' | 'terminated'
  personality: Record<string, unknown>
  model: Record<string, unknown>
  memory: Record<string, unknown>
  tools: Record<string, unknown>
  authority: Record<string, unknown>
  autonomy_level: 'supervised' | 'autonomous' | 'manual'
  strategic_output_mode: null
  personality_preset: null
  tier: null
  model_requirement: null
  hiring_date: string
}

export interface MockCompanyConfig {
  company_name: string
  autonomy_level: 'supervised' | 'autonomous' | 'manual'
  budget_monthly: number
  communication_pattern: 'hub_and_spoke' | 'mesh' | 'hierarchical'
  agents: MockOrgAgent[]
  departments: MockDepartment[]
}

export function makeDepartment(overrides: Partial<MockDepartment> = {}): MockDepartment {
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

export function makeOrgAgent(overrides: Partial<MockOrgAgent> = {}): MockOrgAgent {
  return {
    id: 'agent-001',
    name: 'Alice',
    role: 'engineer',
    department: 'engineering',
    level: 'mid',
    status: 'active',
    personality: {},
    model: {},
    memory: {},
    tools: {},
    authority: {},
    autonomy_level: 'supervised',
    strategic_output_mode: null,
    personality_preset: null,
    tier: null,
    model_requirement: null,
    hiring_date: '2026-01-01T00:00:00Z',
    ...overrides,
  }
}

export function makeCompanyConfig(
  overrides: Partial<MockCompanyConfig> = {},
): MockCompanyConfig {
  return {
    company_name: 'ExampleCorp',
    autonomy_level: 'supervised',
    budget_monthly: 0,
    communication_pattern: 'hub_and_spoke',
    agents: [],
    departments: [makeDepartment()],
    ...overrides,
  }
}

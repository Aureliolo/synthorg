import { http, HttpResponse } from 'msw'
import type {
  getAgent,
  getAgentActivity,
  getAgentHealth,
  getAgentHistory,
  getAgentPerformance,
  getAutonomy,
  listActiveAgents,
  listAgents,
  rollbackAgentIdentity,
  setAutonomy,
  updateAgentModel,
} from '@/api/endpoints/agents'
import type {
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'
import type { AgentHealthResponse, AgentIdentity, AgentIdentityDiff } from '@/api/types'
import type { AgentConfig, AgentPerformanceSummary } from '@/api/types/agents'
import type { AutonomyLevel } from '@/api/types/enums'
import { apiError, apiSuccess, emptyPage, paginatedFor, successFor } from './helpers'

const ALLOWED_AUTONOMY_LEVELS: readonly AutonomyLevel[] = [
  'full',
  'semi',
  'supervised',
  'locked',
]

interface AutonomyValidationError {
  readonly status: number
  readonly message: string
}

interface AutonomyValidationOk {
  readonly level: AutonomyLevel
  readonly reason: string
}

function _normalizeAutonomyBody(raw: unknown): { level?: unknown; reason?: unknown } {
  if (raw !== null && typeof raw === 'object' && !Array.isArray(raw)) {
    return raw
  }
  return {}
}

function _validateAutonomyBody(
  raw: unknown,
): AutonomyValidationOk | AutonomyValidationError {
  const body = _normalizeAutonomyBody(raw)
  if (typeof body.level !== 'string' || body.level.length === 0) {
    return { status: 400, message: "Field 'level' is required" }
  }
  // Backend requires a non-blank reason (>= 3 non-whitespace chars).
  // Guard the type first: a non-string payload must hit the 422 path,
  // not throw on .trim().
  const reason = typeof body.reason === 'string' ? body.reason.trim() : ''
  if (reason.length < 3) {
    return { status: 422, message: "Field 'reason' is required" }
  }
  if (!(ALLOWED_AUTONOMY_LEVELS as readonly string[]).includes(body.level)) {
    return { status: 400, message: 'Unsupported autonomy level' }
  }
  return { level: body.level as AutonomyLevel, reason }
}

/** Minimal AgentConfig stub used when tests do not override. */
export function buildAgent(
  overrides: Partial<AgentConfig> = {},
): AgentConfig {
  return {
    id: 'agent-default',
    name: 'default-agent',
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

/** Minimal valid AgentIdentity stub for the rollback happy path. */
function buildAgentIdentity(
  overrides: Partial<AgentIdentity> = {},
): AgentIdentity {
  return {
    id: 'agent-default',
    name: 'default-agent',
    role: 'engineer',
    department: 'engineering',
    level: 'mid',
    status: 'active',
    autonomy_level: 'supervised',
    strategic_output_mode: null,
    hiring_date: '2026-01-01',
    authority: {
      budget_limit: 0,
      can_approve: [],
      can_delegate_to: [],
      reports_to: null,
    },
    model: {
      provider: 'example-provider',
      model_id: 'example-medium-001',
      model_tier: 'medium',
      fallback_model: null,
      temperature: 0.7,
      max_tokens: 4096,
    },
    memory: {
      type: 'project',
      retention_days: null,
      retention_overrides: [],
    },
    personality: {
      description: 'Balanced default personality.',
      communication_style: 'concise',
      openness: 0.5,
      conscientiousness: 0.5,
      extraversion: 0.5,
      agreeableness: 0.5,
      stress_response: 0.5,
      risk_tolerance: 'medium',
      creativity: 'medium',
      collaboration: 'team',
      decision_making: 'analytical',
      conflict_approach: 'collaborate',
      traits: [],
      verbosity: 'balanced',
    },
    skills: { primary: [], secondary: [] },
    tools: {
      access_level: 'standard',
      allowed: [],
      denied: [],
      mcp_capabilities: [],
      sub_constraints: null,
    },
    ...overrides,
  }
}

function buildHealth(agentId: string): AgentHealthResponse {
  return {
    agent_id: agentId,
    agent_name: 'default-agent',
    last_active_at: null,
    lifecycle_status: 'active',
    performance: null,
    trust: null,
  }
}

function buildPerformance(agentName: string): AgentPerformanceSummary {
  return {
    agent_name: agentName,
    tasks_completed_total: 0,
    tasks_completed_7d: 0,
    tasks_completed_30d: 0,
    avg_completion_time_seconds: null,
    success_rate_percent: null,
    cost_per_task: null,
    quality_score: null,
    collaboration_score: null,
    trend_direction: 'insufficient_data',
    windows: [],
    trends: [],
  }
}

export const agentsHandlers = [
  http.get('/api/v1/agents', () =>
    HttpResponse.json(paginatedFor<typeof listAgents>(emptyPage<AgentConfig>())),
  ),
  // Registered BEFORE ``/agents/:agentId`` so the literal ``active`` path
  // is not captured as an agent id (MSW matches in registration order).
  http.get('/api/v1/agents/active', () =>
    HttpResponse.json(successFor<typeof listActiveAgents>([])),
  ),
  http.get('/api/v1/agents/:agentId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgent>(buildAgent({ id: String(params['agentId']) })),
    ),
  ),
  http.patch('/api/v1/agents/:agentId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof updateAgentModel>(buildAgent({ id: String(params['agentId']) })),
    ),
  ),
  http.get('/api/v1/agents/:agentId/autonomy', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAutonomy>({
        agent_id: String(params['agentId']),
        level: 'supervised',
        promotion_pending: false,
      }),
    ),
  ),
  http.post('/api/v1/agents/:agentId/autonomy', async ({ params, request }) => {
    // request.json() can yield null / array / primitive; the validator
    // normalises to an object so property reads cannot throw, and
    // mirrors the API's 400/422 validation path on bad inputs.
    const raw: unknown = await request.json()
    const validation = _validateAutonomyBody(raw)
    if ('status' in validation) {
      return HttpResponse.json(apiError(validation.message), {
        status: validation.status,
      })
    }
    return HttpResponse.json(
      successFor<typeof setAutonomy>({
        agent_id: String(params['agentId']),
        level: validation.level,
        promotion_pending: false,
      }),
    )
  }),
  http.get('/api/v1/agents/:agentId/performance', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgentPerformance>(
        buildPerformance(`agent-${String(params['agentId'])}`),
      ),
    ),
  ),
  http.get('/api/v1/agents/:agentId/activity', () =>
    HttpResponse.json(paginatedFor<typeof getAgentActivity>(emptyPage())),
  ),
  http.get('/api/v1/agents/:agentId/history', () =>
    HttpResponse.json(successFor<typeof getAgentHistory>([])),
  ),
  http.get('/api/v1/agents/:agentId/health', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgentHealth>(buildHealth(String(params['agentId']))),
    ),
  ),
  http.post('/api/v1/agents/:agentId/versions/rollback', ({ params }) =>
    HttpResponse.json(
      successFor<typeof rollbackAgentIdentity>(
        buildAgentIdentity({ id: String(params['agentId']) }),
      ),
    ),
  ),
  http.get('/api/v1/agents/:agentId/versions', () =>
    HttpResponse.json(
      paginatedFor<VersionHistoryClient<Record<string, unknown>>['list']>(
        emptyPage<VersionSnapshot<Record<string, unknown>>>(),
      ),
    ),
  ),
  http.get('/api/v1/agents/:agentId/versions/diff', ({ params, request }) => {
    const url = new URL(request.url)
    return HttpResponse.json(
      apiSuccess<AgentIdentityDiff>({
        agent_id: String(params['agentId']),
        from_version: Number(url.searchParams.get('from_version') ?? 1),
        to_version: Number(url.searchParams.get('to_version') ?? 2),
        field_changes: [],
        summary: '',
      }),
    )
  }),
]

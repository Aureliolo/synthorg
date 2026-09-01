import { http, HttpResponse } from 'msw'
import type {
  getAgent,
  getAgentActivity,
  getAgentDispatchProfile,
  getAgentHealth,
  getAgentHistory,
  getAgentPerformance,
  getAutonomy,
  listAgents,
  rollbackAgentIdentity,
  setAutonomy,
  updateAgentModel,
} from '@/api/endpoints/agents'
import type {
  VersionHistoryClient,
  VersionSnapshot,
} from '@/api/endpoints/version-history'
import type {
  ActiveAgentSummary,
  AgentConfig,
  AgentHealthResponse,
  AgentIdentity,
  AgentIdentityDiff,
  AgentPerformanceSummary,
  DispatchProfile,
} from '@/api/types/agents'
import type { AutonomyLevel } from '@/api/types/enums'
import { AUTONOMY_LEVEL_VALUES } from '@/api/types/enums'
import { apiError, apiSuccess, emptyPage, emptyPageEnvelope, paginatedFor, successFor } from './helpers'

/**
 * Narrow to a level the backend enum actually declares.
 *
 * Reads the generated constant rather than a hand-written copy: a mock that
 * lists the members itself goes stale the moment one is added or renamed, and
 * then rejects a value the real API accepts while every test still passes. A
 * predicate rather than a membership check plus a cast, so the narrowing is
 * structural.
 */
function isAutonomyLevel(value: string): value is AutonomyLevel {
  return (AUTONOMY_LEVEL_VALUES as readonly string[]).includes(value)
}

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
  if (!isAutonomyLevel(body.level)) {
    return { status: 400, message: 'Unsupported autonomy level' }
  }
  return { level: body.level, reason }
}

/**
 * The one hiring date both stubs use.
 *
 * Date-only, matching the `format: date` the wire carries. Written twice with
 * two shapes, the same conceptual field described two different ways in one
 * file, which is a mock teaching a reader the wrong contract.
 */
const STUB_HIRING_DATE = '2026-01-01'

/** Minimal AgentConfig stub used when tests do not override. */
export function buildAgent(
  overrides: Partial<AgentConfig> = {},
): AgentConfig {
  return {
    id: 'agent-default',
    name: 'default-agent',
    role: 'engineer',
    department: 'engineering',
    status: 'active',
    model: {},
    memory: {},
    tools: {},
    authority: {},
    autonomy_level: 'supervised',
    strategic_output_mode: null,
    capability: null,
    model_requirement: null,
    model_capabilities: null,
    model_capability_status: 'unresolved',
    hiring_date: STUB_HIRING_DATE,
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
    status: 'active',
    autonomy_level: 'supervised',
    strategic_output_mode: null,
    hiring_date: STUB_HIRING_DATE,
    authority: {
      budget_limit: 0,
      can_approve: [],
      can_delegate_to: [],
      reports_to: null,
    },
    model: {
      provider: 'example-provider',
      model_id: 'example-capable-001',
      capability: 'capable',
      temperature: 0.7,
      // The three the API answers null for on an agent nobody pinned them on,
      // which is every agent by default. Each resolves elsewhere per dispatch:
      // the ceiling from settings, the nucleus threshold from the completion
      // config, and the reasoning depth from the per-stakes ladder.
      top_p: null,
      reasoning_effort: null,
      max_tokens: null,
    },
    memory: {
      type: 'project',
      retention_days: null,
      retention_overrides: [],
    },
    skills: { primary: [], secondary: [] },
    tools: {
      access_level: 'standard',
      allowed: [],
      denied: [],
      denied_categories: [],
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
    unavailable: null,
    is_available: true,
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
    trend_direction: 'insufficient_data',
    windows: [],
    trends: [],
  }
}

function buildDispatchProfile(agentId: string): DispatchProfile {
  return {
    agent_id: agentId,
    agent_name: 'default-agent',
    role: 'Developer',
    department: 'Engineering',
    provider_name: 'example-provider',
    model: 'example-capable-001',
    capability: 'capable',
    call_count: 0,
    outcome_counts: {},
    latency: null,
    last_call_at: null,
    min_calls: 20,
    has_enough_calls: false,
    success_rate_percent: 0,
  }
}

export const agentsHandlers = [
  http.get('/api/v1/agents', () =>
    HttpResponse.json(paginatedFor<typeof listAgents>(emptyPage<AgentConfig>())),
  ),
  // Registered BEFORE ``/agents/:agentId`` so the literal ``active`` path
  // is not captured as an agent id (MSW matches in registration order).
  http.get('/api/v1/agents/active', () =>
    // Backend returns a ``PaginatedResponse``; the endpoint walks pages via
    // ``paginateAll`` and returns a flat array, so the wire envelope must stay
    // paginated even when empty.
    HttpResponse.json(emptyPageEnvelope<ActiveAgentSummary>()),
  ),
  // Also literal-before-parameter, for the same reason as ``active``.
  http.get('/api/v1/agents/dispatch-profiles', () =>
    // ``emptyPageEnvelope`` rather than ``paginatedFor``: that helper binds
    // to an endpoint returning a ``PaginatedResult``, and this one walks the
    // pages itself and returns a flat array, exactly like ``active`` above.
    // The wire envelope still has to be paginated.
    HttpResponse.json(emptyPageEnvelope<DispatchProfile>()),
  ),
  http.get('/api/v1/agents/:agentId', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgent>(buildAgent({ id: String(params['agentId']) })),
    ),
  ),
  http.get('/api/v1/agents/:agentId/dispatch-profile', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgentDispatchProfile>(
        buildDispatchProfile(String(params['agentId'])),
      ),
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

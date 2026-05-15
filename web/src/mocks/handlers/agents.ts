import { http, HttpResponse } from 'msw'
import type {
  getAgent,
  getAgentActivity,
  getAgentHistory,
  getAgentPerformance,
  getAutonomy,
  listAgents,
  setAutonomy,
} from '@/api/endpoints/agents'
import type { AgentConfig, AgentPerformanceSummary } from '@/api/types/agents'
import type { AutonomyLevel } from '@/api/types/enums'
import { apiError, emptyPage, paginatedFor, successFor } from './helpers'

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

function buildPerformance(name: string): AgentPerformanceSummary {
  return {
    agent_name: name,
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
  http.get('/api/v1/agents/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgent>(buildAgent({ name: String(params.name) })),
    ),
  ),
  http.get('/api/v1/agents/:agentId/autonomy', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAutonomy>({
        agent_id: String(params.agentId),
        level: 'supervised',
        promotion_pending: false,
      }),
    ),
  ),
  http.post('/api/v1/agents/:agentId/autonomy', async ({ params, request }) => {
    const body = (await request.json()) as { level?: string }
    if (!body.level) {
      return HttpResponse.json(apiError("Field 'level' is required"), {
        status: 400,
      })
    }
    const allowed: readonly AutonomyLevel[] = [
      'full',
      'semi',
      'supervised',
      'locked',
    ]
    if (!(allowed as readonly string[]).includes(body.level)) {
      return HttpResponse.json(apiError('Unsupported autonomy level'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof setAutonomy>({
        agent_id: String(params.agentId),
        level: body.level as AutonomyLevel,
        promotion_pending: false,
      }),
    )
  }),
  http.get('/api/v1/agents/:name/performance', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getAgentPerformance>(buildPerformance(String(params.name))),
    ),
  ),
  http.get('/api/v1/agents/:name/activity', () =>
    HttpResponse.json(paginatedFor<typeof getAgentActivity>(emptyPage())),
  ),
  http.get('/api/v1/agents/:name/history', () =>
    HttpResponse.json(successFor<typeof getAgentHistory>([])),
  ),
]

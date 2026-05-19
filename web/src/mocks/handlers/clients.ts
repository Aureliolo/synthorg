import { http, HttpResponse } from 'msw'
import type {
  approveRequest,
  cancelSimulation,
  ClientProfile,
  ClientRequest,
  createClient,
  decideReviewStage,
  getClient,
  getClientSatisfaction,
  getRequest,
  getReviewPipeline,
  getSimulation,
  getSimulationReport,
  listClients,
  listRequests,
  listSimulations,
  rejectRequest,
  scopeRequest,
  SimulationStatusResponse,
  startSimulation,
  submitRequest,
  TaskRequirement,
  updateClient,
} from '@/api/endpoints/clients'
import { apiError, emptyPage, paginatedFor, successFor, voidSuccess } from './helpers'

export function buildProfile(overrides: Partial<ClientProfile> = {}): ClientProfile {
  return {
    client_id: 'client-default',
    name: 'Default Client',
    persona: 'pragmatic_startup_cto',
    expertise_domains: [],
    strictness_level: 5,
    ...overrides,
  }
}

export function buildRequirement(): TaskRequirement {
  return {
    title: 'Stub requirement',
    description: 'Default requirement for tests',
    task_type: 'development',
    priority: 'medium',
    estimated_complexity: 'medium',
    acceptance_criteria: [],
  }
}

export function buildRequest(overrides: Partial<ClientRequest> = {}): ClientRequest {
  return {
    request_id: 'req-default',
    client_id: 'client-default',
    requirement: buildRequirement(),
    status: 'submitted',
    created_at: '2026-04-19T00:00:00Z',
    metadata: {},
    ...overrides,
  }
}

export function buildSimulation(
  overrides: Partial<SimulationStatusResponse> = {},
): SimulationStatusResponse {
  return {
    simulation_id: 'sim-default',
    status: 'idle',
    config: {
      simulation_id: 'sim-default',
      project_id: 'proj-default',
      rounds: 1,
      clients_per_round: 1,
      requirements_per_client: 1,
    },
    metrics: {
      total_requirements: 0,
      total_tasks_created: 0,
      tasks_accepted: 0,
      tasks_rejected: 0,
      tasks_reworked: 0,
      avg_review_rounds: 0,
      round_metrics: [],
      acceptance_rate: 0,
      rework_rate: 0,
    },
    progress: 0,
    started_at: null,
    completed_at: null,
    error: null,
    ...overrides,
  }
}

export const clientsHandlers = [
  http.get('/api/v1/clients', () =>
    HttpResponse.json(paginatedFor<typeof listClients>(emptyPage())),
  ),
  http.get('/api/v1/clients/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getClient>(buildProfile({ client_id: String(params.id) })),
    ),
  ),
  http.post('/api/v1/clients/', async ({ request }) => {
    const body = (await request.json()) as Partial<ClientProfile>
    if (!body.client_id || !body.name) {
      return HttpResponse.json(apiError("Fields 'client_id' and 'name' are required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof createClient>(
        buildProfile({ ...body, client_id: body.client_id, name: body.name }),
      ),
      { status: 201 },
    )
  }),
  http.patch('/api/v1/clients/:id', async ({ params, request }) => {
    const body = (await request.json()) as Partial<ClientProfile>
    return HttpResponse.json(
      successFor<typeof updateClient>(
        buildProfile({ ...body, client_id: String(params.id) }),
      ),
    )
  }),
  http.delete('/api/v1/clients/:id', () => HttpResponse.json(voidSuccess())),
  http.get('/api/v1/clients/:id/satisfaction', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getClientSatisfaction>({
        client_id: String(params.id),
        total_reviews: 0,
        acceptance_rate: 0,
        average_score: 0,
        history: [],
      }),
    ),
  ),
  http.get('/api/v1/requests', () =>
    HttpResponse.json(paginatedFor<typeof listRequests>(emptyPage())),
  ),
  http.get('/api/v1/requests/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getRequest>(buildRequest({ request_id: String(params.id) })),
    ),
  ),
  http.post('/api/v1/requests/', async ({ request }) => {
    const body = (await request.json()) as { client_id: string }
    return HttpResponse.json(
      successFor<typeof submitRequest>(buildRequest({ client_id: body.client_id })),
      { status: 201 },
    )
  }),
  http.post('/api/v1/requests/:id/approve', ({ params }) =>
    HttpResponse.json(
      successFor<typeof approveRequest>(
        buildRequest({ request_id: String(params.id), status: 'approved' }),
      ),
      // 202 Accepted: approval is acknowledged synchronously; the
      // request runs through the work pipeline in the background and
      // reaches its terminal state asynchronously.
      { status: 202 },
    ),
  ),
  http.post('/api/v1/requests/:id/reject', async ({ params, request }) => {
    const body = (await request.json()) as { reason?: string }
    if (!body.reason) {
      return HttpResponse.json(apiError("Field 'reason' is required"), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof rejectRequest>(
        buildRequest({ request_id: String(params.id), status: 'cancelled' }),
      ),
    )
  }),
  http.post('/api/v1/requests/:id/scope', ({ params }) =>
    HttpResponse.json(
      successFor<typeof scopeRequest>(
        buildRequest({ request_id: String(params.id), status: 'scoping' }),
      ),
    ),
  ),
  http.get('/api/v1/simulations', () =>
    HttpResponse.json(paginatedFor<typeof listSimulations>(emptyPage())),
  ),
  http.get('/api/v1/simulations/:id', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getSimulation>(
        buildSimulation({ simulation_id: String(params.id) }),
      ),
    ),
  ),
  http.post('/api/v1/simulations/', async ({ request }) => {
    const body = (await request.json()) as {
      config?: SimulationStatusResponse['config']
    }
    return HttpResponse.json(
      successFor<typeof startSimulation>(
        buildSimulation({
          simulation_id: body.config?.simulation_id ?? 'sim-new',
          status: 'running',
          ...(body.config ? { config: body.config } : {}),
        }),
      ),
      { status: 201 },
    )
  }),
  http.post('/api/v1/simulations/:id/cancel', ({ params }) =>
    HttpResponse.json(
      successFor<typeof cancelSimulation>(
        buildSimulation({
          simulation_id: String(params.id),
          status: 'cancelled',
        }),
      ),
    ),
  ),
  http.get('/api/v1/simulations/:id/report', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getSimulationReport>({
        format: 'summary',
        simulation_id: String(params.id),
        status: 'completed',
        totals: {},
        rates: {},
      }),
    ),
  ),
  http.get('/api/v1/reviews/:taskId/pipeline', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getReviewPipeline>({
        task_id: String(params.taskId),
        final_verdict: 'skip',
        stage_results: [],
        total_duration_ms: 0,
        reviewed_at: '2026-04-19T00:00:00Z',
      }),
    ),
  ),
  http.post(
    '/api/v1/reviews/:taskId/stages/:stageName/decide',
    async ({ params, request }) => {
      const body = (await request.json()) as { verdict?: 'pass' | 'fail' | 'skip' }
      return HttpResponse.json(
        successFor<typeof decideReviewStage>({
          task_id: String(params.taskId),
          stage_name: String(params.stageName),
          stage_result: {
            stage_name: String(params.stageName),
            verdict: body.verdict ?? 'pass',
            reason: null,
            duration_ms: 0,
            metadata: {},
          },
          pipeline_result: {
            task_id: String(params.taskId),
            final_verdict: body.verdict ?? 'pass',
            stage_results: [],
            total_duration_ms: 0,
            reviewed_at: '2026-04-19T00:00:00Z',
          },
        }),
      )
    },
  ),
]

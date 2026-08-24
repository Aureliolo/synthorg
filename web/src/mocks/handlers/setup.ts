import { http, HttpResponse } from 'msw'
import type {
  completeSetup,
  createAgent,
  createCompany,
  getAgents,
  getAvailableLocales,
  getCompany,
  getModelRecommendations,
  getNameLocales,
  getSetupStatus,
  listTemplates,
  randomizeAgentName,
  saveNameLocales,
  updateAgentModel,
  updateAgentName,
} from '@/api/endpoints/setup'
import type {
  SetupAgentSummary,
  SetupStatusResponse,
} from '@/api/types/setup'
import { apiSuccess, paginatedEnvelopeFor, successFor } from './helpers'

// Per-feature model settings are MODEL_REF, so a recommendation and every
// candidate carry the serialized provider-bound ref the settings write needs.
const MODEL_DEFAULT_REF = JSON.stringify({
  provider: 'test-provider',
  model_id: 'model-default',
})

const EMBED_DEFAULT_REF = JSON.stringify({
  provider: 'test-provider',
  model_id: 'embed-default',
})

const EMBED_BUILTIN_REF = JSON.stringify({
  provider: 'builtin',
  model_id: 'hashing',
})

function buildAgentSummary(
  overrides: Partial<SetupAgentSummary> = {},
): SetupAgentSummary {
  return {
    name: 'setup-agent-default',
    role: 'engineer',
    department: 'engineering',
    model_provider: 'provider-default',
    model_id: 'model-default',
    capability: 'capable',
    ...overrides,
  }
}

const setupComplete: SetupStatusResponse = {
  needs_admin: false,
  needs_setup: false,
  has_providers: true,
  has_name_locales: true,
  has_company: true,
  has_agents: true,
  min_password_length: 12,
}

const setupNeedsAdmin: SetupStatusResponse = {
  needs_admin: true,
  needs_setup: true,
  has_providers: false,
  has_name_locales: false,
  has_company: false,
  has_agents: false,
  min_password_length: 12,
}

// ── Storybook-facing named exports. ──

export const setupStatusComplete = [
  http.get('/api/v1/setup/status', () =>
    HttpResponse.json(apiSuccess(setupComplete)),
  ),
]

export const setupStatusNeedsAdmin = [
  http.get('/api/v1/setup/status', () =>
    HttpResponse.json(apiSuccess(setupNeedsAdmin)),
  ),
]

// ── Default test handlers. ──

export const setupHandlers = [
  http.get('/api/v1/setup/status', () =>
    HttpResponse.json(successFor<typeof getSetupStatus>(setupComplete)),
  ),
  http.get('/api/v1/setup/templates', () =>
    HttpResponse.json(successFor<typeof listTemplates>([])),
  ),
  http.post('/api/v1/setup/company', async ({ request }) => {
    const body = (await request.json()) as { company_name: string }
    return HttpResponse.json(
      successFor<typeof createCompany>({
        company_name: body.company_name,
        description: null,
        template_applied: null,
        department_count: 0,
        currency: 'USD',
        budget: 500,
        model_spend_profile: 'balanced',
        agent_count: 0,
        agents: [],
      }),
      { status: 201 },
    )
  }),
  http.get('/api/v1/setup/company', () =>
    HttpResponse.json(
      successFor<typeof getCompany>({
        company_name: 'Setup Co',
        description: null,
        template_applied: 'startup',
        department_count: 3,
        currency: 'USD',
        budget: 500,
        model_spend_profile: 'balanced',
        agent_count: 1,
        agents: [buildAgentSummary()],
      }),
    ),
  ),
  http.post('/api/v1/setup/agent', async ({ request }) => {
    const body = (await request.json()) as {
      name: string
      role: string
      department: string
      model_provider: string
      model_id: string
    }
    return HttpResponse.json(
      successFor<typeof createAgent>({
        name: body.name,
        role: body.role,
        department: body.department,
        model_provider: body.model_provider,
        model_id: body.model_id,
      }),
      { status: 201 },
    )
  }),
  http.get('/api/v1/setup/agents', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof getAgents>()),
  ),
  http.get('/api/v1/setup/model-recommendations', () =>
    HttpResponse.json(
      successFor<typeof getModelRecommendations>({
        decomposition_recommended: MODEL_DEFAULT_REF,
        model_ref_candidates: [
          {
            provider: 'test-provider',
            model_id: 'model-default',
            ref: MODEL_DEFAULT_REF,
          },
        ],
        embedding_candidates: [
          {
            provider: 'builtin',
            model_id: 'hashing',
            ref: EMBED_BUILTIN_REF,
          },
          {
            provider: 'test-provider',
            model_id: 'embed-default',
            ref: EMBED_DEFAULT_REF,
          },
        ],
        research_recommended: MODEL_DEFAULT_REF,
        cos_recommended: MODEL_DEFAULT_REF,
        propose_recommended: MODEL_DEFAULT_REF,
        routing_recommended: MODEL_DEFAULT_REF,
        narrative_recommended: MODEL_DEFAULT_REF,
        charter_recommended: MODEL_DEFAULT_REF,
      }),
    ),
  ),
  http.put('/api/v1/setup/agents/:index/model', async ({ request }) => {
    const body = (await request.json()) as {
      model_provider: string
      model_id: string
    }
    return HttpResponse.json(
      successFor<typeof updateAgentModel>(
        buildAgentSummary({
          model_provider: body.model_provider,
          model_id: body.model_id,
        }),
      ),
    )
  }),
  http.put('/api/v1/setup/agents/:index/name', async ({ request }) => {
    const body = (await request.json()) as { name: string }
    return HttpResponse.json(
      successFor<typeof updateAgentName>(buildAgentSummary({ name: body.name })),
    )
  }),
  http.post('/api/v1/setup/agents/:index/randomize-name', () =>
    HttpResponse.json(
      successFor<typeof randomizeAgentName>(
        buildAgentSummary({ name: 'random-name' }),
      ),
    ),
  ),
  http.get('/api/v1/setup/name-locales/available', () =>
    HttpResponse.json(
      successFor<typeof getAvailableLocales>({
        regions: {},
        display_names: {},
      }),
    ),
  ),
  http.get('/api/v1/setup/name-locales', () =>
    // Backend returns the ``__all__`` sentinel (never an empty array) when no
    // explicit locales are persisted; mirror that so tests exercise the real shape.
    HttpResponse.json(successFor<typeof getNameLocales>({ locales: ['__all__'] })),
  ),
  http.put('/api/v1/setup/name-locales', async ({ request }) => {
    const body = (await request.json()) as { locales: string[] }
    return HttpResponse.json(
      successFor<typeof saveNameLocales>({ locales: body.locales }),
    )
  }),
  http.post('/api/v1/setup/complete', () =>
    HttpResponse.json(
      successFor<typeof completeSetup>({
        setup_complete: true,
        embedder_selected: true,
        embedder_failure_reason: null,
      }),
    ),
  ),
]

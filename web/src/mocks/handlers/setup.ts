import { http, HttpResponse } from 'msw'
import type {
  completeSetup,
  createAgent,
  createCompany,
  getAvailableLocales,
  getNameLocales,
  getSetupStatus,
  listTemplates,
  randomizeAgentName,
  saveNameLocales,
  updateAgentModel,
  updateAgentName,
  updateAgentPersonality,
} from '@/api/endpoints/setup'
import type { PaginatedResponse } from '@/api/types/http'
import type {
  PersonalityPresetInfo,
  SetupAgentSummary,
  SetupStatusResponse,
} from '@/api/types/setup'
import { apiSuccess, successFor } from './helpers'

function emptyPaginatedEnvelope<T>(): PaginatedResponse<T> {
  return {
    data: [],
    error: null,
    error_detail: null,
    pagination: { limit: 200, next_cursor: null, has_more: false },
    success: true,
  }
}

export function buildAgentSummary(
  overrides: Partial<SetupAgentSummary> = {},
): SetupAgentSummary {
  return {
    name: 'setup-agent-default',
    role: 'engineer',
    department: 'engineering',
    level: 'mid',
    model_provider: 'provider-default',
    model_id: 'model-default',
    tier: 'medium',
    personality_preset: 'balanced',
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
        agent_count: 0,
        agents: [],
      }),
      { status: 201 },
    )
  }),
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
    HttpResponse.json(emptyPaginatedEnvelope<SetupAgentSummary>()),
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
  http.put('/api/v1/setup/agents/:index/personality', async ({ request }) => {
    const body = (await request.json()) as { personality_preset: string }
    return HttpResponse.json(
      successFor<typeof updateAgentPersonality>(
        buildAgentSummary({ personality_preset: body.personality_preset }),
      ),
    )
  }),
  http.get('/api/v1/setup/personality-presets', () =>
    HttpResponse.json(emptyPaginatedEnvelope<PersonalityPresetInfo>()),
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
    HttpResponse.json(successFor<typeof getNameLocales>({ locales: [] })),
  ),
  http.put('/api/v1/setup/name-locales', async ({ request }) => {
    const body = (await request.json()) as { locales: string[] }
    return HttpResponse.json(
      successFor<typeof saveNameLocales>({ locales: body.locales }),
    )
  }),
  http.post('/api/v1/setup/complete', () =>
    HttpResponse.json(
      successFor<typeof completeSetup>({ setup_complete: true }),
    ),
  ),
]

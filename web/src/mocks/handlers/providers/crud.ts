import { http, HttpResponse } from 'msw'
import type {
  createFromPreset,
  createProvider,
  getProvider,
  getProviderModels,
  listPresets,
  listProviders,
  testConnection,
  updateProvider,
} from '@/api/endpoints/providers'
import type {
  CloudPreset,
  LocalPreset,
  ProviderConfig,
} from '@/api/types/providers'
import {
  paginatedEnvelopeFor,
  successFor,
  voidSuccess,
} from '../helpers'

/**
 * Canonical cloud-preset fixture builder.
 *
 * Keeps test fixtures aligned with the shape of the real
 * `/providers/presets` response so tests that need a realistic
 * cloud preset do not diverge from the wire contract.
 */
export function buildCloudPreset(
  overrides: Partial<CloudPreset> = {},
): CloudPreset {
  return {
    kind: 'cloud',
    name: 'cloud-test',
    display_name: 'Cloud Test',
    description: '',
    driver: 'litellm',
    litellm_provider: 'cloud-test',
    auth_type: 'api_key',
    supported_auth_types: ['api_key'],
    default_base_url: null,
    requires_base_url: false,
    is_featured: true,
    default_models: [],
    ...overrides,
  }
}

/**
 * Canonical local-preset fixture builder.
 *
 * Defaults to the local-Ollama shape (the canonical happy path for
 * the wizard's batch-probe flow). Override ``candidate_urls=[]`` for
 * vLLM-style manual-only presets.
 */
export function buildLocalPreset(
  overrides: Partial<LocalPreset> = {},
): LocalPreset {
  return {
    kind: 'local',
    name: 'local-ollama',
    display_name: 'Ollama',
    description: '',
    driver: 'litellm',
    litellm_provider: 'ollama',
    auth_type: 'none',
    default_base_url: 'http://localhost:11434',
    requires_base_url: true,
    is_featured: true,
    candidate_urls: ['http://localhost:11434'],
    supports_model_pull: true,
    supports_model_delete: true,
    supports_model_config: false,
    ...overrides,
  }
}

export function buildProvider(
  overrides: Partial<ProviderConfig> = {},
): ProviderConfig {
  return {
    name: null,
    driver: 'litellm',
    litellm_provider: null,
    auth_type: 'api_key',
    base_url: null,
    models: [],
    has_api_key: false,
    has_oauth_credentials: false,
    has_custom_header: false,
    has_subscription_token: false,
    tos_accepted_at: null,
    oauth_token_url: null,
    oauth_client_id: null,
    oauth_scope: null,
    custom_header_name: null,
    preset_name: null,
    supports_model_pull: false,
    supports_model_delete: false,
    supports_model_config: false,
    ...overrides,
  }
}

export const crudHandlers = [
  http.get('/api/v1/providers', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listProviders>()),
  ),
  http.get('/api/v1/providers/presets', () =>
    HttpResponse.json(successFor<typeof listPresets>([])),
  ),
  http.post('/api/v1/providers/from-preset', async ({ request }) => {
    await request.json()
    return HttpResponse.json(
      successFor<typeof createFromPreset>(
        buildProvider({ preset_name: 'preset-default' }),
      ),
      { status: 201 },
    )
  }),
  http.get('/api/v1/providers/:name', () =>
    HttpResponse.json(successFor<typeof getProvider>(buildProvider())),
  ),
  http.get('/api/v1/providers/:name/models', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof getProviderModels>()),
  ),
  http.post('/api/v1/providers', async ({ request }) => {
    await request.json()
    return HttpResponse.json(
      successFor<typeof createProvider>(buildProvider()),
      { status: 201 },
    )
  }),
  http.put('/api/v1/providers/:name', async ({ request }) => {
    await request.json()
    return HttpResponse.json(successFor<typeof updateProvider>(buildProvider()))
  }),
  http.delete('/api/v1/providers/:name', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.post('/api/v1/providers/:name/test', () =>
    HttpResponse.json(
      successFor<typeof testConnection>({
        success: true,
        latency_ms: 0,
        error: null,
        model_tested: null,
      }),
    ),
  ),
]

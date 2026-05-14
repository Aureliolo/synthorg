import { http, HttpResponse } from 'msw'
import type {
  addAllowlistEntry,
  addProviderModel,
  createFromPreset,
  createProvider,
  discoverModels,
  getDiscoveryPolicy,
  getPresetOverride,
  getProvider,
  getProviderHealth,
  getProviderModels,
  getProviderRateLimits,
  listPresets,
  listProviderAudit,
  listProviders,
  probeLocal,
  removeAllowlistEntry,
  rotateProviderCredentials,
  syncProviderModels,
  testConnection,
  updateModelConfig,
  updatePresetOverride,
  updateProvider,
  updateProviderRateLimits,
} from '@/api/endpoints/providers'
import type {
  CloudPreset,
  LocalPreset,
  PresetOverride,
  ProviderAuditEvent,
  ProviderConfig,
  RateLimitsConfig,
} from '@/api/types/providers'
import {
  paginatedEnvelopeFor,
  paginatedFor,
  successFor,
  voidSuccess,
} from './helpers'

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
 * the wizard's batch-probe flow).  Override ``candidate_urls=[]`` for
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

/**
 * @deprecated Prefer ``buildCloudPreset`` / ``buildLocalPreset`` to
 * make the preset kind explicit at the call site.
 */
export const buildProviderPreset = buildLocalPreset

export function buildProvider(
  overrides: Partial<ProviderConfig> = {},
): ProviderConfig {
  return {
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

const DEFAULT_DISCOVERY_POLICY = {
  host_port_allowlist: [],
  block_private_ips: true,
  entry_count: 0,
} as const

/** Default SSE stream emits one completion event -- suitable for tests that
 * just verify pullModel resolves. Streaming-specific tests should override. */
function buildPullStream(): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder()
  return new ReadableStream<Uint8Array>({
    start(controller) {
      controller.enqueue(
        encoder.encode(
          'event: progress\ndata: {"status":"complete","progress_percent":100,"total_bytes":null,"completed_bytes":null,"error":null,"done":true}\n\n',
        ),
      )
      controller.close()
    },
  })
}

export const providersHandlers = [
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
  http.post('/api/v1/providers/probe-local', () =>
    HttpResponse.json(
      successFor<typeof probeLocal>({
        results: {},
        errors: {},
      }),
    ),
  ),
  http.get('/api/v1/providers/discovery-policy', () =>
    HttpResponse.json(successFor<typeof getDiscoveryPolicy>(DEFAULT_DISCOVERY_POLICY)),
  ),
  http.post('/api/v1/providers/discovery-policy/entries', async ({ request }) => {
    await request.json()
    return HttpResponse.json(
      successFor<typeof addAllowlistEntry>(DEFAULT_DISCOVERY_POLICY),
    )
  }),
  http.post('/api/v1/providers/discovery-policy/remove-entry', async ({ request }) => {
    await request.json()
    return HttpResponse.json(
      successFor<typeof removeAllowlistEntry>(DEFAULT_DISCOVERY_POLICY),
    )
  }),
  http.get('/api/v1/providers/:name', () =>
    HttpResponse.json(successFor<typeof getProvider>(buildProvider())),
  ),
  http.get('/api/v1/providers/:name/models', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof getProviderModels>()),
  ),
  http.get('/api/v1/providers/:name/health', () =>
    HttpResponse.json(
      successFor<typeof getProviderHealth>({
        last_check_timestamp: null,
        avg_response_time_ms: null,
        error_rate_percent_24h: 0,
        calls_last_24h: 0,
        health_status: 'unknown',
        total_tokens_24h: 0,
        total_cost_24h: 0,
      }),
    ),
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
  http.post('/api/v1/providers/:name/discover-models', () =>
    HttpResponse.json(
      successFor<typeof discoverModels>({
        discovered_models: [],
        provider_name: 'provider-default',
      }),
    ),
  ),
  http.post('/api/v1/providers/:name/models/pull', () =>
    new HttpResponse(buildPullStream(), {
      headers: { 'Content-Type': 'text/event-stream' },
    }),
  ),
  http.delete('/api/v1/providers/:name/models/:modelId', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.put('/api/v1/providers/:name/models/:modelId/config', () =>
    HttpResponse.json(
      successFor<typeof updateModelConfig>({
        id: 'model-default',
        alias: null,
        cost_per_1k_input: 0,
        cost_per_1k_output: 0,
        currency: 'USD',
        max_context: 0,
        estimated_latency_ms: null,
        local_params: null,
        supports_tools: false,
        supports_vision: false,
        supports_streaming: false,
      }),
    ),
  ),
  // ── Audit log ─────────────────────────────────────────────────
  http.get('/api/v1/providers/:name/audit', () =>
    HttpResponse.json(
      paginatedFor<typeof listProviderAudit>({
        data: [],
        limit: 50,
        nextCursor: null,
        hasMore: false,
        pagination: {
          limit: 50,
          next_cursor: null,
          has_more: false,
        },
      }),
    ),
  ),
  // ── Rate-limit overrides ──────────────────────────────────────
  http.get('/api/v1/providers/:name/rate-limits', () =>
    HttpResponse.json(
      successFor<typeof getProviderRateLimits>({
        requests_per_minute: 0,
        concurrent_requests: 0,
      }),
    ),
  ),
  http.patch('/api/v1/providers/:name/rate-limits', () =>
    HttpResponse.json(
      successFor<typeof updateProviderRateLimits>({
        requests_per_minute: 0,
        concurrent_requests: 0,
      }),
    ),
  ),
  // ── Preset overrides ──────────────────────────────────────────
  http.get('/api/v1/providers/presets/:presetName/override', () =>
    HttpResponse.json(successFor<typeof getPresetOverride>(null)),
  ),
  http.patch('/api/v1/providers/presets/:presetName/override', ({ params }) =>
    HttpResponse.json(
      successFor<typeof updatePresetOverride>({
        preset_name: String(params.presetName),
        default_models: null,
        supported_auth_types: null,
        candidate_urls: null,
        base_url: null,
        updated_at: '2026-04-28T00:00:00+00:00',
        updated_by: 'test-actor',
      }),
    ),
  ),
  http.delete('/api/v1/providers/presets/:presetName/override', () =>
    HttpResponse.json(voidSuccess()),
  ),
  // ── Credentials rotation ──────────────────────────────────────
  http.post('/api/v1/providers/:name/credentials/rotate', () =>
    HttpResponse.json(
      successFor<typeof rotateProviderCredentials>(buildProvider()),
    ),
  ),
  // ── Manual model add ──────────────────────────────────────────
  http.post('/api/v1/providers/:name/models', () =>
    HttpResponse.json(
      successFor<typeof addProviderModel>(buildProvider()),
    ),
  ),
  // ── Bulk model sync ───────────────────────────────────────────
  http.post('/api/v1/providers/:name/models/sync', () =>
    HttpResponse.json(
      successFor<typeof syncProviderModels>({
        added: [],
        removed: [],
        updated: [],
        models: [],
      }),
    ),
  ),
]

// ── Fixture builders for the new capability shapes ────────────────────

export function buildProviderAuditEvent(
  overrides: Partial<ProviderAuditEvent> = {},
): ProviderAuditEvent {
  return {
    id: 1,
    provider_name: 'provider-default',
    event_type: 'provider_updated',
    actor: { id: 'test-actor', label: 'Test Operator' },
    payload: {},
    occurred_at: '2026-04-28T00:00:00+00:00',
    ...overrides,
  }
}

export function buildRateLimitsConfig(
  overrides: Partial<RateLimitsConfig> = {},
): RateLimitsConfig {
  return {
    requests_per_minute: 0,
    concurrent_requests: 0,
    ...overrides,
  }
}

export function buildPresetOverride(
  overrides: Partial<PresetOverride> = {},
): PresetOverride {
  return {
    preset_name: 'preset-default',
    default_models: null,
    supported_auth_types: null,
    candidate_urls: null,
    base_url: null,
    updated_at: '2026-04-28T00:00:00+00:00',
    updated_by: 'test-actor',
    ...overrides,
  }
}

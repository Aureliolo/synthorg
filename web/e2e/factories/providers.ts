/**
 * Provider mock-data builders.
 */

/**
 * Provider config, mirroring ``ProviderConfig`` from ``@/api/types``
 * (the wire shape ``listProviders`` paginates over ``/providers`` and
 * keys into a ``Record<string, ProviderConfig>`` by ``name``). The
 * minimal earlier shape crashed the provider card, which reads
 * ``models`` and the ``has_*`` credential flags.
 */
export interface MockProvider {
  name: string
  driver: string
  litellm_provider: string | null
  auth_type: string
  base_url: string | null
  models: string[]
  has_api_key: boolean
  has_oauth_credentials: boolean
  has_custom_header: boolean
  has_subscription_token: boolean
  tos_accepted_at: string | null
  oauth_token_url: string | null
  oauth_client_id: string | null
  oauth_scope: string | null
  custom_header_name: string | null
  preset_name: string | null
  supports_model_pull: boolean
  supports_model_delete: boolean
  supports_model_config: boolean
}

export function makeProvider(overrides: Partial<MockProvider> = {}): MockProvider {
  return {
    name: 'example-provider',
    driver: 'litellm',
    litellm_provider: null,
    auth_type: 'api_key',
    base_url: null,
    models: [],
    has_api_key: true,
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

export interface MockProviderHealth {
  provider_name: string
  status: 'up' | 'degraded' | 'down' | 'unknown'
  latency_ms: number | null
  error_detail: string | null
  checked_at: string
}

export function makeProviderHealth(
  overrides: Partial<MockProviderHealth> = {},
): MockProviderHealth {
  return {
    provider_name: 'example-provider',
    status: 'up',
    latency_ms: 42,
    error_detail: null,
    checked_at: '2026-04-01T12:00:00Z',
    ...overrides,
  }
}

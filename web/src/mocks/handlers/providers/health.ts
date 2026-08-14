import { http, HttpResponse } from 'msw'
import type {
  addAllowlistEntry,
  discoverModels,
  getDiscoveryPolicy,
  getProviderHealth,
  probeLocal,
  recheckAllProviderHealth,
  recheckProviderHealth,
  removeAllowlistEntry,
} from '@/api/endpoints/providers'
import { successFor } from '../helpers'

const DEFAULT_DISCOVERY_POLICY = {
  host_port_allowlist: [],
  block_private_ips: true,
  entry_count: 0,
} as const

const UNKNOWN_HEALTH = {
  last_check_timestamp: null,
  avg_response_time_ms: null,
  error_rate_percent_24h: 0,
  calls_last_24h: 0,
  health_status: 'unknown',
  liveness_calls: 0,
  liveness_error_rate_percent: 0,
  total_tokens_24h: 0,
  total_cost_24h: 0,
} as const

// A recheck has just called the provider, so its happy path has a call in the
// window and a derived verdict. ``unknown`` means nothing has called it at
// all, which this endpoint cannot return.
const RECHECKED_HEALTH = {
  last_check_timestamp: '2026-01-01T00:00:00Z',
  avg_response_time_ms: 12,
  error_rate_percent_24h: 0,
  calls_last_24h: 1,
  health_status: 'up',
  liveness_calls: 1,
  liveness_error_rate_percent: 0,
  total_tokens_24h: 0,
  total_cost_24h: 0,
} as const

export const healthHandlers = [
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
  http.post('/api/v1/providers/health/recheck', () =>
    HttpResponse.json(
      successFor<typeof recheckAllProviderHealth>({
        'test-provider': RECHECKED_HEALTH,
      }),
    ),
  ),
  http.get('/api/v1/providers/:name/health', () =>
    HttpResponse.json(successFor<typeof getProviderHealth>(UNKNOWN_HEALTH)),
  ),
  http.post('/api/v1/providers/:name/health/recheck', () =>
    HttpResponse.json(successFor<typeof recheckProviderHealth>(RECHECKED_HEALTH)),
  ),
  http.post('/api/v1/providers/:name/discover-models', ({ params }) =>
    HttpResponse.json(
      successFor<typeof discoverModels>({
        discovered_models: [],
        provider_name: String(params['name'] ?? 'provider-default'),
      }),
    ),
  ),
]

import { http, HttpResponse } from 'msw'
import type {
  addAllowlistEntry,
  discoverModels,
  getDiscoveryPolicy,
  getProviderHealth,
  probeLocal,
  removeAllowlistEntry,
} from '@/api/endpoints/providers'
import { successFor } from '../helpers'

export const DEFAULT_DISCOVERY_POLICY = {
  host_port_allowlist: [],
  block_private_ips: true,
  entry_count: 0,
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
  http.post('/api/v1/providers/:name/discover-models', ({ params }) =>
    HttpResponse.json(
      successFor<typeof discoverModels>({
        discovered_models: [],
        provider_name: String(params.name ?? 'provider-default'),
      }),
    ),
  ),
]

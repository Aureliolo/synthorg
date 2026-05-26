import { http, HttpResponse } from 'msw'
import type {
  getProviderRateLimits,
  updateProviderRateLimits,
} from '@/api/endpoints/providers'
import type { RateLimitsConfig } from '@/api/types'
import { successFor } from '../helpers'

function buildRateLimitsConfig(
  overrides: Partial<RateLimitsConfig> = {},
): RateLimitsConfig {
  return {
    requests_per_minute: 0,
    concurrent_requests: 0,
    ...overrides,
  }
}

export const rateLimitsHandlers = [
  http.get('/api/v1/providers/:name/rate-limits', () =>
    HttpResponse.json(
      successFor<typeof getProviderRateLimits>(buildRateLimitsConfig()),
    ),
  ),
  http.patch('/api/v1/providers/:name/rate-limits', () =>
    HttpResponse.json(
      successFor<typeof updateProviderRateLimits>(buildRateLimitsConfig()),
    ),
  ),
]

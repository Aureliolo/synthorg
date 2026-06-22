import { http, HttpResponse } from 'msw'
import type { resolveSsrfViolation } from '@/api/endpoints/ssrf-violations'
import type { SsrfViolationDTO } from '@/api/types'
import { emptyPageEnvelope, successFor } from './helpers'

const BASE = '/api/v1/providers/ssrf-violations'

export function buildSsrfViolation(
  overrides: Partial<SsrfViolationDTO> = {},
): SsrfViolationDTO {
  return {
    id: 'ssrf-1',
    blocked_range: '169.254.0.0/16',
    hostname: 'metadata.internal',
    port: 80,
    provider_name: 'example-provider',
    resolved_at: null,
    resolved_by: null,
    resolved_ip: '169.254.169.254',
    status: 'pending',
    timestamp: '2026-06-15T09:00:00+00:00',
    url: 'http://metadata.internal/latest/meta-data/',
    ...overrides,
  }
}

export const ssrfViolationsHandlers = [
  http.get(`${BASE}/`, () => HttpResponse.json(emptyPageEnvelope<SsrfViolationDTO>())),
  http.post(`${BASE}/:id/resolve`, async ({ params, request }) => {
    const body = (await request.json()) as { status: 'allowed' | 'denied' }
    return HttpResponse.json(
      successFor<typeof resolveSsrfViolation>(
        buildSsrfViolation({ id: String(params['id']), status: body.status }),
      ),
    )
  }),
]

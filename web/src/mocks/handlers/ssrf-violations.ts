import { http, HttpResponse } from 'msw'
import type { resolveSsrfViolation } from '@/api/endpoints/ssrf-violations'
import type { SsrfViolationDTO } from '@/api/types'
import { apiError, emptyPageEnvelope, successFor } from './helpers'

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
    const body = (await request.json()) as { status?: unknown }
    // Validate the discriminator at runtime instead of force-casting, so the
    // mock mirrors the real endpoint's contract and rejects malformed requests.
    if (body.status !== 'allowed' && body.status !== 'denied') {
      return HttpResponse.json(apiError('status must be "allowed" or "denied"'), {
        status: 400,
      })
    }
    return HttpResponse.json(
      successFor<typeof resolveSsrfViolation>(
        buildSsrfViolation({ id: String(params['id']), status: body.status }),
      ),
    )
  }),
]

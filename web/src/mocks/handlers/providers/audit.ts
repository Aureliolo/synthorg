import { http, HttpResponse } from 'msw'
import type { listProviderAudit } from '@/api/endpoints/providers'
import type { ProviderAuditEvent } from '@/api/types'
import { paginatedFor } from '../helpers'

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

export const auditHandlers = [
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
]

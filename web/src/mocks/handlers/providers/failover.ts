import { http, HttpResponse } from 'msw'
import type {
  getFailoverDeclaration,
  listFailoverEvents,
} from '@/api/endpoints/providers'
import type { FailoverDeclaration } from '@/api/types/providers'
import { paginatedFor, successFor } from '../helpers'

const BASE = '/api/v1/providers/failover'

function buildDeclaration(): FailoverDeclaration {
  return {
    enabled: true,
    routes: [
      {
        declared_provider: 'example-provider',
        declared_model: 'example-expert-001',
        alternate_provider: 'test-provider',
        alternate_model: 'example-capable-001',
      },
    ],
  }
}

export const failoverHandlers = [
  http.get(BASE, () =>
    HttpResponse.json(successFor<typeof getFailoverDeclaration>(buildDeclaration())),
  ),
  http.get(`${BASE}-events`, () =>
    HttpResponse.json(
      paginatedFor<typeof listFailoverEvents>({
        // One pre-flight engagement: the pair was already known unserviceable
        // and never tried, which is the shape the panel has to render.
        data: [
          {
            id: '00000000-0000-4000-8000-000000000001',
            occurred_at: '2026-08-13T11:59:00Z',
            feature: 'engine.reasoning_model',
            declared_provider: 'example-provider',
            declared_model: 'example-expert-001',
            served_provider: 'test-provider',
            served_model: 'example-capable-001',
            trigger_class: 'overloaded',
            trigger_stage: 'preflight',
            agent_id: null,
            task_id: null,
          },
        ],
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

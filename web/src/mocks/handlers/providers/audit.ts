import { http, HttpResponse } from 'msw'
import type { listProviderAudit } from '@/api/endpoints/providers'
import { paginatedFor } from '../helpers'

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

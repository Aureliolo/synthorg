import { http, HttpResponse } from 'msw'
import type {
  getSingleIntegrationHealth,
  listIntegrationHealth,
} from '@/api/endpoints/integration-health'
import type { HealthReport } from '@/api/types/integrations'
import { emptyPage, paginatedFor, successFor } from './helpers'

const NOW = '2026-04-11T12:00:00Z'

// Default test handlers: empty list.
export const integrationHealthHandlers = [
  http.get('/api/v1/integrations/health', () =>
    HttpResponse.json(
      paginatedFor<typeof listIntegrationHealth>(emptyPage<HealthReport>()),
    ),
  ),
  http.get('/api/v1/integrations/health/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getSingleIntegrationHealth>({
        connection_name: String(params['name']),
        status: 'unknown',
        latency_ms: null,
        error_detail: null,
        checked_at: NOW,
        consecutive_failures: 0,
      }),
    ),
  ),
]

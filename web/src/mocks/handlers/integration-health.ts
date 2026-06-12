import { http, HttpResponse } from 'msw'
import type {
  getSingleIntegrationHealth,
  listIntegrationHealth,
} from '@/api/endpoints/integration-health'
import type { HealthReport } from '@/api/types/integrations'
import { emptyPage, paginatedFor, successFor } from './helpers'

/** Build a one-shot paginated page wrapping the provided reports. */
function paginatedPage(reports: readonly HealthReport[]): {
  data: HealthReport[]
  total: number
  offset: number
  limit: number
  nextCursor: string | null
  hasMore: boolean
  pagination: {
    total: number
    offset: number
    limit: number
    next_cursor: string | null
    has_more: boolean
  }
} {
  // Mirror the endpoint default page size (50) so frontend tests
  // that rely on pagination defaults stay aligned with the wire
  // contract.
  const limit = 50
  return {
    data: [...reports],
    total: reports.length,
    offset: 0,
    limit,
    nextCursor: null,
    hasMore: false,
    pagination: {
      total: reports.length,
      offset: 0,
      limit,
      next_cursor: null,
      has_more: false,
    },
  }
}

const NOW = '2026-04-11T12:00:00Z'

// Storybook export: populated health reports for existing stories.
const mockHealthReports: HealthReport[] = [
  {
    connection_name: 'primary-github',
    status: 'healthy',
    latency_ms: 42,
    error_detail: null,
    checked_at: NOW,
    consecutive_failures: 0,
  },
  {
    connection_name: 'ops-smtp',
    status: 'unhealthy',
    latency_ms: null,
    error_detail: 'Connection refused',
    checked_at: NOW,
    consecutive_failures: 4,
  },
]

export const integrationHealthList = [
  http.get('/api/v1/integrations/health', () =>
    HttpResponse.json(
      paginatedFor<typeof listIntegrationHealth>(paginatedPage(mockHealthReports)),
    ),
  ),
  http.get('/api/v1/integrations/health/:name', ({ params }) => {
    const report = mockHealthReports.find((r) => r.connection_name === params['name'])
    return HttpResponse.json(
      successFor<typeof getSingleIntegrationHealth>(
        report ?? {
          connection_name: String(params['name']),
          status: 'unknown',
          latency_ms: null,
          error_detail: null,
          checked_at: NOW,
          consecutive_failures: 0,
        },
      ),
    )
  }),
]

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

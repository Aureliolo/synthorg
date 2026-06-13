import { http, HttpResponse } from 'msw'
import type {
  coordinateTask,
  listCoordinationMetrics,
} from '@/api/endpoints/coordination'
import { DEFAULT_CURRENCY } from '@/utils/currencies'
import { paginatedEnvelopeFor, successFor } from './helpers'

export const coordinationHandlers = [
  http.post('/api/v1/tasks/:id/coordinate', ({ params }) =>
    HttpResponse.json(
      successFor<typeof coordinateTask>({
        parent_task_id: String(params['id']),
        topology: 'auto',
        total_duration_seconds: 0,
        total_cost: 0,
        currency: DEFAULT_CURRENCY,
        phases: [],
        wave_count: 0,
        is_success: true,
      }),
    ),
  ),
  http.get('/api/v1/coordination/metrics', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listCoordinationMetrics>()),
  ),
]

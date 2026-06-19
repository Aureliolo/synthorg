import { http, HttpResponse } from 'msw'
import type {
  listAssignments,
  listVariants,
  registerVariant,
} from '@/api/endpoints/experiments'
import { paginatedEnvelopeFor, successFor } from './helpers'

export const experimentsHandlers = [
  http.get('/api/v1/experiments/:experiment/variants', () =>
    HttpResponse.json(successFor<typeof listVariants>([])),
  ),
  http.get('/api/v1/experiments/:experiment/assignments', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listAssignments>()),
  ),
  http.post('/api/v1/experiments/:experiment/variants', ({ params }) =>
    HttpResponse.json(
      successFor<typeof registerVariant>({
        experiment: String(params['experiment']),
        variant: 'variant-default',
        weight: 1,
        description: '',
        created_at: '2026-05-20T12:00:00Z',
      }),
    ),
  ),
]

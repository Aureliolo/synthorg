import { http, HttpResponse } from 'msw'
import type { listAssignments, listVariants } from '@/api/endpoints/experiments'
import { paginatedEnvelopeFor, successFor } from './helpers'

export const experimentsHandlers = [
  http.get('/api/v1/experiments/:experiment/variants', () =>
    HttpResponse.json(successFor<typeof listVariants>([])),
  ),
  http.get('/api/v1/experiments/:experiment/assignments', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listAssignments>()),
  ),
]

import { http, HttpResponse } from 'msw'
import type { listPatterns, listRecommendations } from '@/api/endpoints/meta-analytics'
import { paginatedEnvelopeFor } from './helpers'

export const metaAnalyticsHandlers = [
  http.get('/api/v1/meta/analytics/patterns', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listPatterns>()),
  ),
  http.get('/api/v1/meta/analytics/recommendations', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof listRecommendations>()),
  ),
]

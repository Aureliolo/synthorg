import { http, HttpResponse } from 'msw'
import type {
  ScalingDecisionResponse,
  ScalingSignalResponse,
  ScalingStrategyResponse,
  getScalingDecisions,
  triggerScalingEvaluation,
} from '@/api/endpoints/scaling'
import type { PaginatedResponse } from '@/api/types/http'
import { emptyPage, paginatedFor, successFor } from './helpers'

function emptyPaginatedEnvelope<T>(): PaginatedResponse<T> {
  return {
    data: [],
    error: null,
    error_detail: null,
    pagination: { limit: 200, next_cursor: null, has_more: false },
    success: true,
  }
}

export const scalingHandlers = [
  http.get('/api/v1/scaling/strategies', () =>
    HttpResponse.json(emptyPaginatedEnvelope<ScalingStrategyResponse>()),
  ),
  http.get('/api/v1/scaling/decisions', () =>
    HttpResponse.json(
      paginatedFor<typeof getScalingDecisions>(
        emptyPage<ScalingDecisionResponse>(50),
      ),
    ),
  ),
  http.get('/api/v1/scaling/signals', () =>
    HttpResponse.json(emptyPaginatedEnvelope<ScalingSignalResponse>()),
  ),
  http.post('/api/v1/scaling/evaluate', () =>
    HttpResponse.json(successFor<typeof triggerScalingEvaluation>([])),
  ),
]

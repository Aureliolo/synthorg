import { http, HttpResponse } from 'msw'
import type {
  ScalingDecisionResponse,
  ScalingSignalResponse,
  ScalingStrategyResponse,
  getScalingDecisions,
  triggerScalingEvaluation,
} from '@/api/endpoints/scaling'
import {
  emptyPage,
  emptyPaginatedEnvelope,
  paginatedFor,
  successFor,
} from './helpers'

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

import { http, HttpResponse } from 'msw'
import type {
  ScalingDecisionResponse,
  getScalingDecisions,
  getScalingSignals,
  getScalingStrategies,
  triggerScalingEvaluation,
  updateScalingPriority,
  updateScalingStrategy,
} from '@/api/endpoints/scaling'
import {
  emptyPage,
  paginatedEnvelopeFor,
  paginatedFor,
  successFor,
} from './helpers'

export const scalingHandlers = [
  http.get('/api/v1/scaling/strategies', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof getScalingStrategies>()),
  ),
  http.get('/api/v1/scaling/decisions', () =>
    HttpResponse.json(
      paginatedFor<typeof getScalingDecisions>(
        emptyPage<ScalingDecisionResponse>(50),
      ),
    ),
  ),
  http.get('/api/v1/scaling/signals', () =>
    HttpResponse.json(paginatedEnvelopeFor<typeof getScalingSignals>()),
  ),
  http.post('/api/v1/scaling/evaluate', () =>
    HttpResponse.json(successFor<typeof triggerScalingEvaluation>([])),
  ),
  http.put('/api/v1/scaling/strategies/:name', ({ params }) =>
    HttpResponse.json(
      successFor<typeof updateScalingStrategy>({
        name: String(params['name']),
        enabled: true,
        priority: 0,
      }),
    ),
  ),
  http.put('/api/v1/scaling/priority', () =>
    HttpResponse.json(successFor<typeof updateScalingPriority>([])),
  ),
]

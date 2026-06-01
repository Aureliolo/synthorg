import { http, HttpResponse } from 'msw'
import type { getLearningCurve } from '@/api/endpoints/learning'
import { successFor } from './helpers'

export const learningHandlers = [
  http.get('/api/v1/learning/curve', () =>
    HttpResponse.json(
      successFor<typeof getLearningCurve>({
        points: [],
        has_regression: false,
        latest_total: null,
      }),
    ),
  ),
]

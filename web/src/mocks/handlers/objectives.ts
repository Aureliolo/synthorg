import { http, HttpResponse } from 'msw'
import type { submitObjective } from '@/api/endpoints/objectives'
import { successFor } from './helpers'

export const objectivesHandlers = [
  http.post('/api/v1/objectives', () =>
    HttpResponse.json(
      successFor<typeof submitObjective>({
        submission_id: '00000000-0000-0000-0000-000000000001',
        status: 'accepted',
      }),
      { status: 202 },
    ),
  ),
]

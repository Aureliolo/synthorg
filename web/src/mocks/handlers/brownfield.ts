import { http, HttpResponse } from 'msw'
import type { importCodebase } from '@/api/endpoints/brownfield'
import { successFor } from './helpers'

export const brownfieldHandlers = [
  http.post('/api/v1/brownfield/import', () =>
    HttpResponse.json(
      successFor<typeof importCodebase>({
        project_id: 'project-default',
        status: 'accepted',
      }),
      { status: 202 },
    ),
  ),
]

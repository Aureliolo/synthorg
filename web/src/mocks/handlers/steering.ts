import { http, HttpResponse } from 'msw'

import type {
  confirmSupersession,
  issueSteering,
  listActiveSteering,
} from '@/api/endpoints/steering'

import { successFor } from './helpers'

interface SupersedeBody {
  readonly task_ids?: readonly string[]
}

export const steeringHandlers = [
  http.post('/api/v1/cockpit/steering', () =>
    HttpResponse.json(
      successFor<typeof issueSteering>({
        directive_id: 'directive-1',
        kind: 'redirect',
        superseded_task_ids: [],
        proposal: null,
      }),
    ),
  ),
  http.get('/api/v1/cockpit/steering', () =>
    HttpResponse.json(
      successFor<typeof listActiveSteering>([
        {
          entry_id: 'directive-1',
          kind: 'redirect',
          text: 'use Postgres not Mongo',
          author: 'mission-control',
          recorded_at: '2026-05-22T12:00:00Z',
          narrow_task_ids: [],
          narrow_agent_ids: [],
          requires_replan: true,
        },
      ]),
    ),
  ),
  http.post(
    '/api/v1/cockpit/steering/:directiveId/supersede',
    async ({ params, request }) => {
      let body: SupersedeBody
      try {
        body = (await request.json()) as SupersedeBody
      } catch {
        body = {}
      }
      return HttpResponse.json(
        successFor<typeof confirmSupersession>({
          directive_id: String(params['directiveId']),
          cancelled_task_ids: body.task_ids ?? [],
        }),
      )
    },
  ),
]

import { http, HttpResponse } from 'msw'

import type { addPlanComment, listPlanComments } from '@/api/endpoints/plan-comments'
import type { PlanItemComment } from '@/api/types/plans'

import { successFor } from './helpers'

function buildComment(overrides: Partial<PlanItemComment> = {}): PlanItemComment {
  return {
    id: 'comment-1',
    plan_id: 'plan-default',
    item_id: 'item-1',
    author: 'reviewer',
    body: 'Looks good to me.',
    created_at: '2026-07-02T10:00:00Z',
    ...overrides,
  }
}

export const planCommentsHandlers = [
  http.get('/api/v1/plans/:planId/comments', ({ params }) =>
    HttpResponse.json(
      successFor<typeof listPlanComments>([
        buildComment({ plan_id: String(params['planId']) }),
      ]),
    ),
  ),
  http.post(
    '/api/v1/plans/:planId/comments/items/:itemId',
    async ({ params, request }) => {
      const body = (await request.json()) as { body: string }
      return HttpResponse.json(
        successFor<typeof addPlanComment>(
          buildComment({
            id: 'comment-new',
            plan_id: String(params['planId']),
            item_id: String(params['itemId']),
            body: body.body,
          }),
        ),
        { status: 201 },
      )
    },
  ),
]

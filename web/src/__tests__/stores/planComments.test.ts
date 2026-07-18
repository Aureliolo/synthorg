import { http, HttpResponse } from 'msw'
import { beforeEach, describe, expect, it } from 'vitest'

import type { PlanItemComment } from '@/api/types/plans'
import { apiSuccess } from '@/mocks/handlers'
import { usePlanCommentsStore } from '@/stores/planComments'
import { server } from '@/test-setup'

function comment(id: string, body = 'A note'): PlanItemComment {
  return {
    id,
    plan_id: 'plan-1',
    item_id: 'item-1',
    author: 'reviewer',
    author_kind: 'human',
    author_agent_id: null,
    reply_to_id: null,
    body,
    created_at: '2026-07-01T10:00:00Z',
  }
}

describe('usePlanCommentsStore', () => {
  beforeEach(() => {
    usePlanCommentsStore.getState().reset()
  })

  it('hydrates the thread on fetch', async () => {
    server.use(
      http.get('/api/v1/plans/:planId/comments', () =>
        HttpResponse.json(apiSuccess([comment('c1'), comment('c2')])),
      ),
    )
    await usePlanCommentsStore.getState().fetchComments('plan-1')
    expect(usePlanCommentsStore.getState().comments.map((c) => c.id)).toEqual(['c1', 'c2'])
  })

  it('appends the posted comment and reconciles the thread from the backend', async () => {
    // The POST returns the operator's comment; a follow-up reconcile re-lists
    // the thread, so the backend truth (here the operator's comment plus the
    // responsible role's inline reply) is what ends up on screen.
    const agentReply: PlanItemComment = {
      ...comment('agent', 'The runway holds.'),
      author: 'Casey',
      author_kind: 'agent',
      author_agent_id: 'agent-cfo',
      reply_to_id: 'new',
    }
    server.use(
      http.post('/api/v1/plans/:planId/comments/items/:itemId', () =>
        HttpResponse.json(apiSuccess(comment('new')), { status: 201 }),
      ),
      http.get('/api/v1/plans/:planId/comments', () =>
        HttpResponse.json(apiSuccess([comment('new'), agentReply])),
      ),
    )
    const result = await usePlanCommentsStore.getState().addComment('plan-1', 'item-1', 'Hi')
    expect(result?.id).toBe('new')
    expect(usePlanCommentsStore.getState().comments.map((c) => c.id)).toEqual([
      'new',
      'agent',
    ])
  })

  it('does not duplicate a comment a WS echo already appended', async () => {
    usePlanCommentsStore.setState({ comments: [comment('echoed')] })
    server.use(
      http.post('/api/v1/plans/:planId/comments/items/:itemId', () =>
        HttpResponse.json(apiSuccess(comment('echoed'))),
      ),
    )
    await usePlanCommentsStore.getState().addComment('plan-1', 'item-1', 'Hi')
    expect(usePlanCommentsStore.getState().comments).toHaveLength(1)
  })

  it('returns null and does not append on a failed post', async () => {
    server.use(
      http.post('/api/v1/plans/:planId/comments/items/:itemId', () =>
        HttpResponse.json({ success: false }, { status: 500 }),
      ),
    )
    const result = await usePlanCommentsStore.getState().addComment('plan-1', 'item-1', 'Hi')
    expect(result).toBeNull()
    expect(usePlanCommentsStore.getState().comments).toEqual([])
  })
})

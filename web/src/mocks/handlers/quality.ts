import { http, HttpResponse } from 'msw'
import type {
  getQualityOverride,
  setQualityOverride,
} from '@/api/endpoints/quality'
import { successFor, voidSuccess } from './helpers'

export const qualityHandlers = [
  http.get('/api/v1/agents/:id/quality/override', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getQualityOverride>({
        agent_id: String(params['id']),
        score: 0,
        reason: 'default',
        applied_by: 'system',
        applied_at: '2026-04-19T00:00:00Z',
        expires_at: null,
      }),
    ),
  ),
  http.post('/api/v1/agents/:id/quality/override', async ({ params, request }) => {
    const body = (await request.json()) as { score: number; reason: string }
    return HttpResponse.json(
      successFor<typeof setQualityOverride>({
        agent_id: String(params['id']),
        score: body.score,
        reason: body.reason,
        applied_by: 'user-1',
        applied_at: '2026-04-19T00:00:00Z',
        expires_at: null,
      }),
    )
  }),
  http.delete('/api/v1/agents/:id/quality/override', () =>
    HttpResponse.json(voidSuccess()),
  ),
]

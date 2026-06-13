import { http, HttpResponse } from 'msw'
import type {
  getCalibration,
  getCollaborationScore,
  getOverride,
  setOverride,
} from '@/api/endpoints/collaboration'
import { apiError, successFor, voidSuccess } from './helpers'

export const collaborationHandlers = [
  http.get('/api/v1/agents/:id/collaboration/score', () =>
    HttpResponse.json(
      successFor<typeof getCollaborationScore>({
        score: 0,
        strategy_name: 'default',
        component_scores: [],
        confidence: 0,
        override_active: false,
      }),
    ),
  ),
  http.get('/api/v1/agents/:id/collaboration/override', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getOverride>({
        agent_id: String(params['id']),
        score: 0,
        reason: 'default',
        applied_by: 'system',
        applied_at: '2026-04-19T00:00:00Z',
        expires_at: null,
      }),
    ),
  ),
  http.post('/api/v1/agents/:id/collaboration/override', async ({ params, request }) => {
    const body = (await request.json()) as {
      score?: number
      reason?: string
      expires_in_days?: number | null
    }
    if (typeof body.score !== 'number' || !body.reason) {
      return HttpResponse.json(apiError("Fields 'score' and 'reason' are required"), {
        status: 400,
      })
    }
    const appliedAt = '2026-04-19T00:00:00Z'
    const expiresAt =
      typeof body.expires_in_days === 'number'
        ? new Date(
            new Date(appliedAt).getTime() +
              body.expires_in_days * 24 * 60 * 60 * 1000,
          ).toISOString()
        : null
    return HttpResponse.json(
      successFor<typeof setOverride>({
        agent_id: String(params['id']),
        score: body.score,
        reason: body.reason,
        applied_by: 'user-1',
        applied_at: appliedAt,
        expires_at: expiresAt,
      }),
    )
  }),
  http.delete('/api/v1/agents/:id/collaboration/override', () =>
    HttpResponse.json(voidSuccess()),
  ),
  http.get('/api/v1/agents/:id/collaboration/calibration', ({ params }) =>
    HttpResponse.json(
      successFor<typeof getCalibration>({
        agent_id: String(params['id']),
        average_drift: null,
        records: [],
        record_count: 0,
      }),
    ),
  ),
]

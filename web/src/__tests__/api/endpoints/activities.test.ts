import { http, HttpResponse } from 'msw'
import { listActivities } from '@/api/endpoints/activities'
import { pageEnvelope } from '@/mocks/handlers/helpers'
import { server } from '@/test-setup'
import type { ActivityEvent } from '@/api/types/analytics'

/**
 * The REST /activities feed must carry the same failure-aware run outcome as
 * the WS path: a terminal task event maps to a run_outcome so ActivityFeedItem
 * renders the badge + danger dot, while non-task events carry none. This is the
 * REST-side counterpart to the WS wsEventToActivityItem coverage.
 */

function event(overrides: Partial<ActivityEvent>): ActivityEvent {
  return {
    event_type: 'task_completed',
    timestamp: '2026-07-10T12:00:00+00:00',
    description: 'Task did a thing',
    related_ids: { agent_id: 'alice', task_id: 'task-1' },
    ...overrides,
  }
}

describe('listActivities run-outcome mapping', () => {
  it('maps task_completed to a succeeded outcome', async () => {
    server.use(
      http.get('/api/v1/activities', () =>
        HttpResponse.json(pageEnvelope<ActivityEvent>([event({})])),
      ),
    )
    const result = await listActivities()
    expect(result.data[0]?.run_outcome).toBe('succeeded')
  })

  it('maps task_failed to a failed outcome', async () => {
    server.use(
      http.get('/api/v1/activities', () =>
        HttpResponse.json(
          pageEnvelope<ActivityEvent>([
            event({ event_type: 'task_failed', description: 'Task failed' }),
          ]),
        ),
      ),
    )
    const result = await listActivities()
    expect(result.data[0]?.run_outcome).toBe('failed')
  })

  it('leaves non-task events without a run outcome', async () => {
    server.use(
      http.get('/api/v1/activities', () =>
        HttpResponse.json(
          pageEnvelope<ActivityEvent>([
            event({
              event_type: 'hired',
              description: 'Agent hired',
              related_ids: { agent_id: 'bob' },
            }),
          ]),
        ),
      ),
    )
    const result = await listActivities()
    const item = result.data[0]
    expect(item?.run_outcome).toBeNull()
    expect(item?.task_id).toBeNull()
    expect(item?.agent_name).toBe('bob')
  })
})

import { http, HttpResponse } from 'msw'
import { listActivities } from '@/api/endpoints/activities'
import { pageEnvelope } from '@/mocks/handlers/helpers'
import { server } from '@/test-setup'
import { SYSTEM_ACTOR_NAME, UNKNOWN_AGENT_NAME } from '@/utils/agents'
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
    description: 'Task succeeded',
    related_ids: { agent_id: 'agent-1', task_id: 'task-1' },
    actor_name: 'Anica Hocevar',
    subject_title: 'Wire the login page',
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

  it('maps task_empty to an empty outcome, distinct from failed', async () => {
    server.use(
      http.get('/api/v1/activities', () =>
        HttpResponse.json(
          pageEnvelope<ActivityEvent>([
            event({
              event_type: 'task_empty',
              description: 'Task produced no artifacts',
            }),
          ]),
        ),
      ),
    )
    const result = await listActivities()
    expect(result.data[0]?.run_outcome).toBe('empty')
  })

  it('leaves non-task events without a run outcome', async () => {
    server.use(
      http.get('/api/v1/activities', () =>
        HttpResponse.json(
          pageEnvelope<ActivityEvent>([
            event({
              event_type: 'hired',
              description: 'Agent hired',
              related_ids: { agent_id: 'agent-2' },
              actor_name: 'Daler Rumaysayev',
            }),
          ]),
        ),
      ),
    )
    const result = await listActivities()
    const item = result.data[0]
    expect(item?.run_outcome).toBeNull()
    expect(item?.task_id).toBeNull()
    expect(item?.agent_name).toBe('Daler Rumaysayev')
  })
})

/**
 * The feed used to assign ``related_ids.agent_id`` to the field it renders as a
 * name, so every row was headed by a UUID. There are three actor states and none
 * of them is the reference itself.
 */
describe('listActivities actor naming', () => {
  async function firstItemFrom(overrides: Partial<ActivityEvent>) {
    server.use(
      http.get('/api/v1/activities', () =>
        HttpResponse.json(pageEnvelope<ActivityEvent>([event(overrides)])),
      ),
    )
    const result = await listActivities()
    return result.data[0]
  }

  it('names the actor the backend resolved', async () => {
    const item = await firstItemFrom({ actor_name: 'Feline Rek' })
    expect(item?.agent_name).toBe('Feline Rek')
  })

  it('says the agent is unknown when the roster no longer covers them', async () => {
    const item = await firstItemFrom({
      related_ids: { agent_id: '1e831131-2a1c-5284-8e62-269465cb3626' },
      actor_name: null,
    })
    expect(item?.agent_name).toBe(UNKNOWN_AGENT_NAME)
    expect(item?.agent_name).not.toContain('1e831131')
  })

  it('names the system for work belonging to no agent', async () => {
    const item = await firstItemFrom({ related_ids: {}, actor_name: null })
    expect(item?.agent_name).toBe(SYSTEM_ACTOR_NAME)
  })

  it('leads the description with the task title when there is one', async () => {
    const item = await firstItemFrom({
      description: 'Task produced no artifacts',
      subject_title: 'Wire the login page',
    })
    expect(item?.description).toBe('Wire the login page: Task produced no artifacts')
  })

  it('leaves the description alone when no task is named', async () => {
    const item = await firstItemFrom({
      description: 'Agent hired',
      subject_title: null,
    })
    expect(item?.description).toBe('Agent hired')
  })
})

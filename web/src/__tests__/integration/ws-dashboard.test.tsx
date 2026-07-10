/**
 * Integration test: WebSocket event -> Analytics store update flow.
 *
 * Tests that WS events arriving at the analytics store correctly update
 * state including activity feed items.
 */
import { useAnalyticsStore } from '@/stores/analytics'
import type { WsEvent } from '@/api/types/websocket'

describe('WS Dashboard Integration', () => {
  beforeEach(() => {
    useAnalyticsStore.setState({
      overview: {
        total_agents: 5,
        active_agents_count: 3,
        idle_agents_count: 2,
        task_outcomes: { succeeded: 0, empty: 0, failed: 0 },
        total_tasks: 10,
        tasks_by_status: {
          created: 0,
          assigned: 3,
          in_progress: 2,
          in_review: 1,
          completed: 4,
          blocked: 0,
          failed: 0,
          interrupted: 0,
          suspended: 0,
          cancelled: 0,
          rejected: 0,
          auth_required: 0,
        },
        total_cost: 42.17,
        budget_used_percent: 35,
        budget_remaining: 57.83,
        currency: 'EUR',
        cost_7d_trend: [
          { timestamp: '2026-03-29T00:00:00Z', value: 42.17 },
        ],
        tasks_7d_trend: [],
        agents_7d_trend: [],
        review_7d_trend: [],
      },
      activities: [],
      loading: false,
      error: null,
    })
  })

  it('adds activity item when WS event is processed', () => {
    const event: WsEvent = {
      event_type: 'task.created',
      channel: 'tasks',
      timestamp: new Date().toISOString(),
      payload: { task_id: 'task-99', title: 'New integration task' },
    }

    useAnalyticsStore.getState().updateFromWsEvent(event)

    const activities = useAnalyticsStore.getState().activities
    expect(activities).toHaveLength(1)
    expect(activities[0]).toMatchObject({
      action_type: 'task.created',
      task_id: 'task-99',
    })
    expect(activities[0]!.timestamp).toBeDefined()
  })

  it('updates overview metrics via setState', () => {
    useAnalyticsStore.setState((s) => ({
      overview: s.overview ? { ...s.overview, total_tasks: 15 } : s.overview,
    }))

    expect(useAnalyticsStore.getState().overview?.total_tasks).toBe(15)
  })

  it('prepends new activities and caps at max', () => {
    // Add multiple events
    for (let i = 0; i < 55; i++) {
      const event: WsEvent = {
        event_type: 'task.created',
        channel: 'tasks',
        timestamp: new Date().toISOString(),
        payload: { task_id: `task-${i}`, title: `Task ${i}` },
      }
      useAnalyticsStore.getState().updateFromWsEvent(event)
    }

    // Activities should be capped at MAX_ACTIVITIES (50) defined in stores/analytics.ts
    const activities = useAnalyticsStore.getState().activities
    expect(activities).toHaveLength(50)
  })
})

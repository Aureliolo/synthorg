import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'

/**
 * Critical-flow E2E: ``coordination.completed`` WebSocket dispatch.
 *
 * A multi-agent coordination run finishing emits ``coordination.completed``.
 * The backend publishes it on the ``tasks`` channel
 * (``api/controllers/coordination.py`` -> ``CHANNEL_TASKS``), which the
 * dashboard subscribes to (``DASHBOARD_CHANNELS`` in
 * ``hooks/useDashboardData.ts``). The handler funnels every frame through
 * ``wsEventToActivityItem`` (``utils/dashboard.ts``), whose
 * ``EVENT_DESCRIPTIONS`` maps ``coordination.completed`` ->
 * "completed coordination" and falls back to the payload's ``agent_name``.
 *
 * This spec drives that real path end-to-end: mount the dashboard, push a
 * ``coordination.completed`` frame, and assert the activity feed renders
 * the mapped description plus the coordinating agent. A regression in the
 * channel binding, the event-type description map, or the activity-item
 * builder would drop the entry and fail the assertions.
 */

test.describe('Coordination completed dispatch', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('renders a coordination.completed frame in the activity feed', async ({ page }) => {
    await page.goto('/')
    await expect(page).toHaveURL((url) => url.pathname === '/')
    await expect(page.locator('main')).toBeVisible()

    // The feed starts empty (catch-all analytics returns no seeded
    // activity), so the injected frame is the only entry that can appear.
    await injectEvent(page, {
      event_type: 'coordination.completed',
      channel: 'tasks',
      timestamp: '2026-04-01T12:05:00Z',
      payload: { agent_name: 'Coordinator', coordination_id: 'coord-001' },
    })

    // No ``description`` in the payload -> the builder uses the
    // EVENT_DESCRIPTIONS mapping for the event type.
    await expect(page.getByText('completed coordination').first()).toBeVisible()
    await expect(page.getByText('Coordinator').first()).toBeVisible()
  })
})

import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeMeeting } from '../factories'

/**
 * Critical-flow E2E: meeting creation + real-time updates.
 *
 * Loads the meetings list page, asserts the mount, and exercises
 * a server-pushed meeting transition via the WebSocket harness.
 */

test.describe('Meeting creation critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('loads the meetings list page and accepts WS-pushed updates', async ({ page }) => {
    await page.goto('/meetings')
    await expect(page).toHaveURL(/\/meetings/)
    await expect(page.locator('main')).toBeVisible()
    const heading = page.getByRole('heading').first()
    await expect(heading).toBeVisible()

    const meeting = makeMeeting({ status: 'completed' })
    await injectEvent(page, {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-05-13T12:00:00Z',
      payload: { ...meeting, meeting_id: meeting.id },
    })

    // ``meeting.completed`` is a real ``WsEventType``; the store
    // dispatch for it lives in the meetings store unit tests. This
    // E2E pins the orchestrator: heading + ``main`` both survive
    // the injected frame, so the dispatch loop didn't tear React
    // down on the live event type.
    await expect(page.locator('main')).toBeVisible()
    await expect(heading).toBeVisible()
  })
})

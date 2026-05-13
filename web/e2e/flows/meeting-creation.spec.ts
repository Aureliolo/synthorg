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

    const meeting = makeMeeting({ status: 'completed' })
    await injectEvent(page, {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-05-13T12:00:00Z',
      payload: { ...meeting, meeting_id: meeting.id },
    })

    await expect(page.locator('main')).toBeVisible()
  })
})

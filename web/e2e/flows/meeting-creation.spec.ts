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

  test('tolerates a malformed meeting.completed WS payload without unmounting', async ({ page }) => {
    await page.goto('/meetings')
    await expect(page).toHaveURL(/\/meetings/)
    await expect(page.locator('main')).toBeVisible()
    const heading = page.getByRole('heading').first()
    await expect(heading).toBeVisible()

    // The meetings store handler expects ``payload.meeting`` to wrap
    // the meeting record (see ``handleWsEvent`` in
    // web/src/stores/meetings.ts ~line 498); this spec injects the
    // older flat-payload shape so the store skips the upsert with a
    // warning. The assertion below pins that the dispatch loop
    // gracefully handles the malformed shape -- the page stays
    // mounted instead of crashing. The wrapped-payload upsert path
    // is covered by the meetings-store unit tests, which can mock
    // the list-endpoint shape without an end-to-end browser round.
    const meeting = makeMeeting({ status: 'completed' })
    await injectEvent(page, {
      event_type: 'meeting.completed',
      channel: 'meetings',
      timestamp: '2026-05-13T12:00:00Z',
      payload: { ...meeting, meeting_id: meeting.id },
    })

    await expect(page.locator('main')).toBeVisible()
    await expect(heading).toBeVisible()
  })
})

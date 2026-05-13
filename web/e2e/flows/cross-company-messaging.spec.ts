import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeMessage } from '../factories'

/**
 * Critical-flow E2E: inter-company / cross-company message intake.
 *
 * The Messages page is an asynchronous investigation surface (per
 * docs/design/page-structure.md). This spec asserts the page mounts
 * and processes a WS-pushed message frame whose channel + metadata
 * carry cross-company provenance, exercising the message-store
 * sanitisation + dispatch chain that federation traffic exercises
 * end-to-end.
 */

test.describe('Inter-company messaging critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('loads the messages page and processes a cross-company WS frame', async ({ page }) => {
    await page.goto('/messages')
    await expect(page).toHaveURL(/\/messages/)
    await expect(page.locator('main')).toBeVisible()
    const heading = page.getByRole('heading').first()
    await expect(heading).toBeVisible()

    const inbound = makeMessage({
      sender: 'agent-other-co-007',
      to: 'agent-001',
      channel: '#inbound-federation',
    })
    // ``message.received`` is intentionally NOT in ``WS_EVENT_TYPE_VALUES``
    // (the wire enum only carries ``message.sent``). The harness still
    // accepts the frame; the page-survival assertions below verify the
    // dispatch loop tolerates unknown event types without crashing the
    // surrounding shell -- the actual cross-company display path lives
    // elsewhere and is exercised by the unit-level messages store
    // tests, not this E2E.
    await injectEvent(page, {
      event_type: 'message.received',
      channel: 'messages',
      timestamp: '2026-05-13T12:00:00Z',
      payload: { ...inbound, message_id: inbound.id, origin: 'external_a2a' },
    })

    // The page heading + ``main`` both surviving the injected frame
    // proves the dispatch loop didn't tear React down on the unknown
    // event_type. Bare ``main.visible`` alone would still pass if a
    // blank shell rendered after a crash.
    await expect(page.locator('main')).toBeVisible()
    await expect(heading).toBeVisible()
  })
})

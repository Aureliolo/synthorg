import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeProvider, makeProviderHealth } from '../factories'

/**
 * Critical-flow E2E: provider probe.
 *
 * Pushes a server-side health-check result via the WebSocket harness
 * so the dashboard's provider-health badge surface processes the
 * event without errors.
 */

test.describe('Provider probe critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Catch-all FIRST so the specific stub below wins (Playwright
    // matches handlers LIFO).
    await mockApiRoutes(page)
    await page.route('**/api/v1/providers', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [makeProvider()],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test('loads providers and processes a WS health event', async ({ page }) => {
    await page.goto('/providers')
    await expect(page).toHaveURL(/\/providers/)
    await expect(page.locator('main')).toBeVisible()
    // Confirm the seeded provider rendered before injecting the event;
    // otherwise the assertion below would pass on an empty page.
    await expect(page.getByText('example-provider').first()).toBeVisible()

    await injectEvent(page, {
      event_type: 'system.error',
      channel: 'system',
      timestamp: '2026-04-01T12:00:00Z',
      payload: {
        message: 'example-provider degraded: latency 250ms',
        ...makeProviderHealth({ status: 'degraded', latency_ms: 250 }),
      },
    })
    // The notifications store enqueues a "System error" entry for
    // ``system.error`` events. Asserting that title is visible proves
    // the WS frame conformed to the runtime validator (``isWsEvent``)
    // and reached the registered handler chain. ``provider.*`` events
    // are not yet in ``WS_EVENT_TYPE_VALUES``, so we use the closest
    // generic channel that the dashboard already wires up; the
    // provider-list rerender is also asserted separately to catch a
    // surface teardown regression.
    await expect(page.getByText('System error').first()).toBeVisible()
    await expect(page.getByText('example-provider').first()).toBeVisible()
  })
})

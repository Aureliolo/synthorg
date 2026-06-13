import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeProvider, makeProviderHealth } from '../factories'

/**
 * Critical-flow E2E: provider list mount + WS notification intake.
 *
 * Asserts the providers list mounts with the seeded provider, the
 * provider row click handler works, and the dashboard processes a
 * WS frame without crashing. The spec does NOT assert that the
 * provider-health badge updates in response to the WS frame because
 * ``provider.*`` event types are not (yet) in the dashboard's
 * ``WS_EVENT_TYPE_VALUES`` enum and no provider store subscribes to
 * health-change events (only notifications consume the closest
 * ``system.error`` mapping). Renaming the spec to reflect this
 * scope is the honest path until provider-health WS routing lands;
 * see ``web/src/api/types/websocket.ts``.
 */

test.describe('Provider list + notification intake', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Catch-all FIRST so the specific stub below wins (Playwright
    // matches handlers LIFO).
    await mockApiRoutes(page)
    await page.route('**/api/v1/providers**', (route) =>
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

  test('loads providers, clicks a row, processes a WS notification', async ({ page }) => {
    await page.goto('/providers')
    await expect(page).toHaveURL(/\/providers/)
    await expect(page.locator('main')).toBeVisible()
    // Confirm the seeded provider rendered before injecting the event;
    // otherwise the assertion below would pass on an empty page.
    await expect(page.getByText('example-provider').first()).toBeVisible()

    // Real UI interaction: click the provider entry. The full manual
    // "probe now" trigger requires a backend that this E2E doesn't
    // simulate, but the click-into-detail path is what users actually
    // exercise; a regression in the click handler would surface here.
    await page.getByText('example-provider').first().click()
    await expect(page.locator('main')).toBeVisible()

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

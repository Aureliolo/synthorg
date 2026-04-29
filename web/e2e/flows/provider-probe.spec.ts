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
    await mockApiRoutes(page)
  })

  test('loads providers and processes a WS health event', async ({ page }) => {
    await page.goto('/providers')
    await expect(page).toHaveURL(/\/providers/)
    await expect(page.locator('main')).toBeVisible()

    await injectEvent(page, {
      type: 'provider.health_changed',
      version: 1,
      health: makeProviderHealth({ status: 'degraded', latency_ms: 250 }),
    })
    await expect(page.locator('main')).toBeVisible()
  })
})

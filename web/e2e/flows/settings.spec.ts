import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'

/**
 * Critical-flow E2E: settings management.
 *
 * Exercises the settings root + per-namespace deep link. The full
 * edit-save-reload round-trip requires a setting schema that maps
 * to a known editable widget; this spec asserts the page mounts
 * and reacts to the WS `system.restart_required` event without
 * blanking out, which is the wedge the audit flagged.
 */

test.describe('Settings management critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('loads the settings index and a namespace deep-link', async ({ page }) => {
    await page.goto('/settings')
    await expect(page).toHaveURL(/\/settings/)
    await expect(page.locator('main')).toBeVisible()

    await page.goto('/settings/api')
    await expect(page).toHaveURL(/\/settings\/api/)
    await expect(page.locator('main')).toBeVisible()
  })

  test('tolerates an unknown WS event_type (system.restart_required) without unmounting', async ({ page }) => {
    await page.goto('/settings')
    await expect(page.locator('main')).toBeVisible()
    const heading = page.getByRole('heading').first()
    await expect(heading).toBeVisible()

    // ``system.restart_required`` is a NotificationCategory (see
    // ``web/src/types/notifications.ts``), NOT a top-level
    // ``WsEventType`` (only ``system.error`` / ``startup`` /
    // ``shutdown`` are wire event types). The actual restart-required
    // surface is the notification-store toast/drawer, not a WS-driven
    // page banner. This spec deliberately injects an unknown wire
    // event type to verify the dispatch loop tolerates it without
    // unmounting; the unit tests for the notification store cover
    // the restart-required toast path.
    await injectEvent(page, {
      event_type: 'system.restart_required',
      channel: 'system',
      timestamp: '2026-05-13T12:00:00Z',
      payload: { reason: 'config_change', namespace: 'api', key: 'rate_limit_per_minute' },
    })

    await expect(page.locator('main')).toBeVisible()
    await expect(heading).toBeVisible()
  })
})

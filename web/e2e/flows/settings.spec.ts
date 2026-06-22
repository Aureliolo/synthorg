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

  test('edits a setting value and saves it via PUT', async ({ page }) => {
    // One editable integer setting in the ``api`` namespace. The settings
    // page loads ALL settings (GET /settings/_schema + GET /settings) and
    // filters to the namespace client-side, so both endpoints must carry
    // the same definition for the field to render and the value to bind.
    const definition = {
      namespace: 'api',
      key: 'rate_limit_per_minute',
      type: 'int',
      default: '60',
      description: 'Requests per minute per authenticated user.',
      enum_values: [],
      env_var_override: null,
      group: 'Rate Limiting',
      level: 'basic',
      max_value: 10000,
      min_value: 1,
      read_only_post_init: false,
      restart_required: false,
      sensitive: false,
      validator_pattern: null,
    }
    await page.route('**/api/v1/settings/_schema', (route) =>
      route.fulfill({
        json: { success: true, data: [definition], error: null, error_detail: null },
      }),
    )
    await page.route(/\/api\/v1\/settings\?.*/, (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [{ definition, source: 'default', updated_at: null, value: '60' }],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 100, next_cursor: null, has_more: false },
        },
      }),
    )
    // The PUT the FloatingSaveBar fires on save echoes the new value back.
    await page.route(
      '**/api/v1/settings/api/rate_limit_per_minute',
      (route) => {
        if (route.request().method() !== 'PUT') {
          route.fallback()
          return
        }
        route.fulfill({
          json: {
            success: true,
            data: { definition, source: 'db', updated_at: '2026-05-13T12:00:00Z', value: '120' },
            error: null,
            error_detail: null,
          },
        })
      },
    )

    await page.goto('/settings/api')
    await expect(page.locator('main')).toBeVisible()

    // The field input lives inside the row group keyed by namespace/key.
    const input = page
      .locator('[data-setting-key="api/rate_limit_per_minute"]')
      .getByRole('spinbutton')
    await expect(input).toBeVisible()
    await input.fill('120')

    // Editing marks the row dirty, which mounts the FloatingSaveBar with a
    // Save button. Clicking it drives the PUT round-trip the store owns.
    const [saved] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes('/api/v1/settings/api/rate_limit_per_minute') &&
          res.request().method() === 'PUT',
      ),
      page.getByRole('button', { name: /^Save$/ }).click(),
    ])
    expect(saved.request().method()).toBe('PUT')

    // On a successful save the dirty count drops to zero and the save bar
    // unmounts, so the Save button is no longer present.
    await expect(page.getByRole('button', { name: /^Save$/ })).toHaveCount(0)
  })
})

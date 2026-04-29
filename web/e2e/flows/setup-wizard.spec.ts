import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'

/**
 * Critical-flow E2E: setup wizard.
 *
 * Verifies the wizard root mounts and renders its first-step
 * heading. Multi-step navigation, validation, and submit-side
 * assertions are intentionally not driven through Playwright here
 * because the wizard's later steps require coordinated provider /
 * agent / theme persistence calls; covering each step's persistence
 * round-trip belongs in a follow-up suite once the deterministic
 * setup-config fixture is wired through ``page.route``.
 */

test.describe('Setup wizard critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('loads the setup wizard root with its first-step heading', async ({ page }) => {
    await page.goto('/setup')
    await expect(page).toHaveURL(/\/setup/)
    await expect(page.locator('main')).toBeVisible()
    await expect(page.getByRole('heading').first()).toBeVisible()
  })
})

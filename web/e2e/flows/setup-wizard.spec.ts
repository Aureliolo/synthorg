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
    const heading = page.getByRole('heading').first()
    await expect(heading).toBeVisible()
    // Scope the input lookup to the wizard root so a stray dialog or
    // command-palette input mounted outside the wizard never wins
    // this match. Every wizard step renders at least one named text
    // input; the assertion (not a soft conditional) makes the test
    // fail loudly if the wizard regresses to a no-input shell.
    const wizardRoot = page.locator('main')
    const firstInput = wizardRoot
      .locator('input[type="text"], input:not([type])')
      .first()
    await expect(firstInput).toBeVisible()
    await firstInput.fill('SynthOrg E2E Co')
    await expect(firstInput).toHaveValue('SynthOrg E2E Co')
  })
})

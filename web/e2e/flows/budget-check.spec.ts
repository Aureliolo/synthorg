import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { makeBudgetSnapshot } from '../factories'

/**
 * Critical-flow E2E: budget overview page.
 *
 * Mocks the snapshot endpoint with deterministic budget figures and
 * asserts the page renders without errors.
 */

test.describe('Budget check critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    const snapshot = makeBudgetSnapshot({
      total_cost: 100,
      budget_remaining: 400,
      budget_used_percent: 20,
    })
    await page.route('**/api/v1/budget/snapshot', (route) =>
      route.fulfill({
        json: { success: true, data: snapshot, error: null, error_detail: null },
      }),
    )
    await mockApiRoutes(page)
  })

  test('loads the budget page', async ({ page }) => {
    await page.goto('/budget')
    await expect(page).toHaveURL(/\/budget/)
    await expect(page.locator('main')).toBeVisible()
  })
})

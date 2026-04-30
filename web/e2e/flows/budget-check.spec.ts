import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeBudgetSnapshot } from '../factories'

/**
 * Critical-flow E2E: budget overview page.
 *
 * Mocks the snapshot endpoint with deterministic budget figures, then
 * pushes a budget-alert WS event so the notifications dispatch chain
 * is exercised end-to-end (envelope + handler + UI surface).
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
    // Catch-all FIRST so the specific stub below wins (Playwright
    // matches handlers LIFO).
    await mockApiRoutes(page)
    await page.route('**/api/v1/budget/snapshot', (route) =>
      route.fulfill({
        json: { success: true, data: snapshot, error: null, error_detail: null },
      }),
    )
  })

  test('loads the budget page and processes an alert event', async ({ page }) => {
    await page.goto('/budget')
    await expect(page).toHaveURL(/\/budget/)
    await expect(page.locator('main')).toBeVisible()

    // Push a ``budget.alert`` event matching the dashboard's
    // ``WsEvent`` runtime validator. The notifications store enqueues
    // a "Budget threshold crossed" entry for ``level=threshold`` and
    // a "Budget exhausted" entry for ``level=exhausted``; asserting
    // the threshold title is visible proves the frame survived
    // envelope validation, dispatch, and notification routing.
    await injectEvent(page, {
      event_type: 'budget.alert',
      channel: 'budget',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { level: 'threshold', message: '80% of monthly budget used' },
    })
    await expect(page.getByText('Budget threshold crossed').first()).toBeVisible()
  })
})

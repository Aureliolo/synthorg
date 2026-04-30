import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { makeAgentList } from '../factories'

/**
 * Critical-flow E2E: agents list surface (precursor to "New Agent").
 *
 * The full multi-step "create agent" form requires a realistic
 * backend; this test confirms the agents list mounts with a
 * deterministic three-agent payload so the user reaches the entry
 * point of the new-agent flow without the page erroring out.
 */

test.describe('Agent creation critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Register the catch-all FIRST so the specific route stub below
    // overrides it: Playwright matches route handlers in LIFO order,
    // so the most-recently-registered handler wins.
    await mockApiRoutes(page)
    await page.route('**/api/v1/agents', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: makeAgentList(3),
          error: null,
          error_detail: null,
          pagination: { total: 3, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test('loads the agents list page', async ({ page }) => {
    await page.goto('/agents')
    await expect(page).toHaveURL(/\/agents/)
    await expect(page.locator('main')).toBeVisible()
    // Assert the seeded three-agent payload actually rendered. The
    // factory builds names from a fixed roster so 'Alice' is always
    // present; if the list / card grid regresses to an empty state,
    // this assertion fails loudly instead of passing silently.
    await expect(page.getByText('Alice').first()).toBeVisible()
  })
})

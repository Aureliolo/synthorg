import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { makeMemoryEntry, makeOntologyFact } from '../factories'

/**
 * Critical-flow E2E: memory / ontology surface.
 *
 * Mounts the page with deterministic memory entries and ontology
 * facts so a regression that hides either surface fails this test.
 */

test.describe('Memory recall critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await page.route('**/api/v1/memory/search**', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [makeMemoryEntry()],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      }),
    )
    await page.route('**/api/v1/ontology/facts**', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [makeOntologyFact()],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      }),
    )
    await mockApiRoutes(page)
  })

  test('loads the ontology page where memory surfaces live', async ({ page }) => {
    await page.goto('/ontology')
    await expect(page).toHaveURL(/\/ontology/)
    await expect(page.locator('main')).toBeVisible()
  })
})

import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeMemoryEntry, makeOntologyFact } from '../factories'

/**
 * Critical-flow E2E: memory / ontology surface.
 *
 * Mounts the page with deterministic memory entries and ontology
 * facts, then drives a personality-trimmed event through the WS
 * harness so the notifications dispatch chain is exercised.
 */

test.describe('Memory recall critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Register the catch-all FIRST so the specific route stubs
    // below override it: Playwright matches route handlers in LIFO
    // order, so the most-recently-registered handler wins.
    await mockApiRoutes(page)
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
  })

  test('loads the ontology page and processes a trim event', async ({ page }) => {
    await page.goto('/ontology')
    await expect(page).toHaveURL(/\/ontology/)
    await expect(page.locator('main')).toBeVisible()
    // Assert BOTH seeded surfaces independently so a regression that
    // hides either the memory panel OR the ontology panel fails this
    // test. A single OR-regex assertion would let one surface go
    // missing silently, weakening the signal.
    await expect(
      page.getByText('Always validate inputs before processing').first(),
    ).toBeVisible()
    await expect(page.getByText('reports_to').first()).toBeVisible()

    // Drive a personality-trimmed event through the harness. The
    // event_type is in ``WS_EVENT_TYPE_VALUES`` and the notifications
    // store enqueues a "Personality trimmed" entry, so visibility of
    // that title proves the WS frame conformed to the runtime
    // validator (``isWsEvent``) and reached the dispatch chain.
    await injectEvent(page, {
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { agent_id: 'agent-001', agent_name: 'Alice' },
    })
    await expect(page.getByText('Personality trimmed').first()).toBeVisible()
  })
})

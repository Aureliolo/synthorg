import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeOntologyEntity, makeEntityListMeta } from '../factories'

/**
 * Critical-flow E2E: ontology catalogue + WS personality-trim intake.
 *
 * The ``/ontology`` page renders the entity catalogue from
 * ``listEntities`` -> ``GET /ontology/entities`` (NOT a raw memory/fact
 * feed). This spec seeds one deterministic entity, asserts the catalogue
 * row renders and its detail opens on click, then drives a
 * ``personality.trimmed`` WS frame so the notifications dispatch chain is
 * exercised end-to-end (the entry lands in the notification drawer).
 */

test.describe('Memory recall critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    // Trailing ``**`` so the glob matches the paginated request
    // (``/ontology/entities?limit=...``); the page reads this endpoint
    // (drift reports fall through to the empty catch-all, which is fine).
    await page.route('**/api/v1/ontology/entities**', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [makeOntologyEntity()],
          error: null,
          error_detail: null,
          meta: makeEntityListMeta(),
          pagination: { total: 1, offset: 0, limit: 50, next_cursor: null, has_more: false },
        },
      }),
    )
  })

  test('loads the ontology catalogue and processes a trim event', async ({ page }) => {
    await page.goto('/ontology')
    await expect(page).toHaveURL(/\/ontology/)
    await expect(page.locator('main')).toBeVisible()

    // The seeded entity name renders in the catalogue.
    const entity = page.getByText('TaskAssignment').first()
    await expect(entity).toBeVisible()

    // Real UI interaction: select the entity. A click-handler regression
    // would leave the detail (the entity's definition) unrendered.
    await entity.click()
    await expect(page.getByText('A task assigned to an agent.').first()).toBeVisible()

    // Drive a personality-trimmed event through the harness. The
    // event_type is in ``WS_EVENT_TYPE_VALUES`` and the notifications
    // store's ws-handler enqueues a "Personality trimmed" drawer entry,
    // so its visibility (after opening the drawer) proves the WS frame
    // conformed to the runtime validator (``isWsEvent``) and reached the
    // dispatch chain.
    await injectEvent(page, {
      event_type: 'personality.trimmed',
      channel: 'agents',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { agent_id: 'agent-001', agent_name: 'Alice' },
    })
    await page.getByRole('button', { name: /notifications/i }).click()
    await expect(page.getByText('Personality trimmed').first()).toBeVisible()
  })

  test('filters the catalogue via the entity search box', async ({ page }) => {
    // Two distinct entities so the client-side search has something to
    // narrow. This route wins over the single-entity beforeEach stub
    // (Playwright matches handlers LIFO).
    await page.route('**/api/v1/ontology/entities**', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [
            makeOntologyEntity({
              name: 'TaskAssignment',
              definition: 'A task assigned to an agent.',
            }),
            makeOntologyEntity({
              name: 'BudgetLedger',
              definition: 'A record of company spend.',
            }),
          ],
          error: null,
          error_detail: null,
          meta: makeEntityListMeta({ user_count: 2, total_count: 2 }),
          pagination: { total: 2, offset: 0, limit: 50, next_cursor: null, has_more: false },
        },
      }),
    )

    await page.goto('/ontology')
    await expect(page.getByText('TaskAssignment').first()).toBeVisible()
    await expect(page.getByText('BudgetLedger').first()).toBeVisible()

    // Typing into the search box narrows the catalogue to the matching
    // entity; the non-matching one drops out of the grid. The filter is
    // client-side (ontology store ``searchQuery``), so no request fires.
    await page.getByPlaceholder(/search entities/i).fill('BudgetLedger')
    await expect(page.getByText('BudgetLedger').first()).toBeVisible()
    await expect(page.getByText('TaskAssignment')).toHaveCount(0)
  })
})

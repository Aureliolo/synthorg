import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeOntologyEntity } from '../factories'

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
          pagination: { total: 1, offset: 0, limit: 50 },
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
})

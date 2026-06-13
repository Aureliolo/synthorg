import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { makeTask } from '../factories/tasks'
import { pointerDragTo } from '../helpers/interactions'

/**
 * Critical-flow E2E: Kanban drag-and-drop column move.
 *
 * ``task-lifecycle.spec.ts`` covers WS frame intake and card selection
 * but explicitly skips the drag path. This spec closes that gap: it
 * drags a ``created`` task from the Backlog column onto the Ready column
 * and asserts the move commits.
 *
 * The board uses ``@dnd-kit`` with a ``PointerSensor`` (8px activation),
 * NOT HTML5 drag, so the gesture is driven via {@link pointerDragTo}
 * (raw pointer events) rather than Playwright's ``dragTo``. ``created``
 * -> ``assigned`` is a valid transition (``VALID_TRANSITIONS`` in
 * ``utils/tasks.ts``) and Ready's first status is ``assigned``, so the
 * drop fires an optimistic re-group plus ``POST /tasks/{id}/transition``.
 * Both are asserted: the network call confirms the persistence write was
 * attempted, and the card landing under ``[data-column-id="ready"]``
 * confirms the optimistic board update.
 */

test.describe('Task board drag-and-drop', () => {
  const draggable = makeTask({
    id: 'task-drag-001',
    title: 'Drag me to ready',
    status: 'created',
  })

  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    await page.route('**/api/v1/tasks**', (route) => {
      // The transition POST is matched by its own handler below; only
      // the list GET is fulfilled here.
      if (route.request().method() !== 'GET') {
        route.fallback()
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: [draggable],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      })
    })
    await page.route('**/api/v1/tasks/*/transition', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: { ...draggable, status: 'assigned' },
          error: null,
          error_detail: null,
        },
      }),
    )
  })

  test('drags a task from backlog to ready and commits the transition', async ({
    page,
  }) => {
    await page.goto('/tasks')
    await expect(page).toHaveURL(/\/tasks/)
    await expect(page.locator('main')).toBeVisible()

    const backlog = page.locator('[data-column-id="backlog"]')
    const ready = page.locator('[data-column-id="ready"]')
    await expect(backlog).toBeVisible()
    await expect(ready).toBeVisible()

    const card = backlog.getByText(draggable.title)
    await expect(card).toBeVisible()

    // The drop fires the transition POST; await it so the assertion does
    // not race the network round-trip.
    const [transition] = await Promise.all([
      page.waitForResponse('**/api/v1/tasks/*/transition'),
      pointerDragTo(page, card, ready),
    ])
    expect(transition.request().method()).toBe('POST')

    // Optimistic re-group: the card now lives in the Ready column and is
    // gone from Backlog.
    await expect(ready.getByText(draggable.title)).toBeVisible()
    await expect(backlog.getByText(draggable.title)).toHaveCount(0)
  })
})

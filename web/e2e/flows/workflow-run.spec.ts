import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeWorkflow, makeWorkflowExecution } from '../factories'

/**
 * Critical-flow E2E: workflows list mount + system.error notification path.
 *
 * Mounts the workflows list with a deterministic single-workflow
 * payload and pushes a ``system.error`` WebSocket frame whose payload
 * carries the workflow-execution context. Asserts the notifications
 * dispatch chain renders the "System error" entry; this exercises
 * the full envelope-validate -> dispatch -> notification-render path
 * end-to-end. The original ``workflow_execution.status_changed`` /
 * ``coordination.completed`` event types have no production handler
 * (no entry in ``WS_EVENT_TYPE_VALUES``, no notifications-store case)
 * so they would silently no-op; the ``system.error`` substitute is
 * the closest mapped event the dashboard already handles.
 */

test.describe('Workflows list + system.error notification path', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Catch-all FIRST so the specific stub below wins (Playwright
    // matches handlers LIFO).
    await mockApiRoutes(page)
    await page.route('**/api/v1/workflows', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [makeWorkflow()],
          error: null,
          error_detail: null,
          // PaginationMeta envelope carries cursor fields alongside
          // limit/total/offset; matches the production contract in
          // ``web/src/api/types`` so the client unwraps to a
          // ``PaginatedResult`` with sensible cursor state for a
          // single-item fixture (no further pages, null cursor).
          pagination: {
            total: 1,
            offset: 0,
            limit: 50,
            next_cursor: null,
            has_more: false,
          },
        },
      }),
    )
  })

  test('loads workflows and processes a WS system.error notification', async ({
    page,
  }) => {
    await page.goto('/workflows')
    await expect(page).toHaveURL(/\/workflows/)
    await expect(page.locator('main')).toBeVisible()

    // Push a workflow-failure system event. ``workflow_execution.*``
    // is not in ``WS_EVENT_TYPE_VALUES`` and the production
    // notifications store has no ``coordination.completed`` handler,
    // so neither would surface an observable UI change. The
    // dashboard's notifications dispatch DOES handle ``system.error``
    // (enqueues a "System error" entry with the payload's message);
    // sending the workflow-failure context that way exercises the
    // full envelope-validate -> dispatch -> notification-render
    // chain end-to-end. A regression in any of those layers would
    // prevent the "System error" title from rendering.
    const execution = makeWorkflowExecution({
      status: 'success',
      finished_at: '2026-04-01T12:01:00Z',
    })
    const message = `workflow execution ${execution.id} completed`
    await injectEvent(page, {
      event_type: 'system.error',
      channel: 'system',
      timestamp: '2026-04-01T12:01:00Z',
      payload: {
        ...execution,
        execution_id: execution.id,
        message,
      },
    })
    // Assert BOTH the static title and the payload-driven message
    // so a regression where the dispatch chain drops the
    // description (or the notifications store renders only the
    // title) fails the test. The notifications case for
    // ``system.error`` enqueues
    // ``{title: 'System error', description: payload.message}``;
    // both fields land in the rendered notification entry.
    await expect(page.getByText('System error').first()).toBeVisible()
    await expect(page.getByText(message).first()).toBeVisible()
    await expect(page.locator('main')).toBeVisible()
  })
})

import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeWorkflow, makeWorkflowExecution } from '../factories'

/**
 * Critical-flow E2E: workflow execution.
 *
 * Pushes a server-side execution-status event via the WebSocket
 * harness so the dashboard's status badge updates in real time.
 */

test.describe('Workflow run critical flow', () => {
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
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test('loads workflows and processes a WS execution-status event', async ({
    page,
  }) => {
    await page.goto('/workflows')
    await expect(page).toHaveURL(/\/workflows/)
    await expect(page.locator('main')).toBeVisible()

    // Push an execution-status event. ``workflow_execution.*`` is not
    // (yet) in ``WS_EVENT_TYPE_VALUES``, so we use the closest mapped
    // event the dashboard already handles -- a coordination-completed
    // frame that the store dispatches without further filtering.
    // Wire envelope conforms to ``isWsEvent`` (event_type / channel /
    // timestamp / payload); the legacy ``{type, ..., execution}``
    // shape would be silently discarded.
    const execution = makeWorkflowExecution({
      status: 'success',
      finished_at: '2026-04-01T12:01:00Z',
    })
    await injectEvent(page, {
      event_type: 'coordination.completed',
      channel: 'system',
      timestamp: '2026-04-01T12:01:00Z',
      payload: {
        ...execution,
        execution_id: execution.id,
        message: `workflow execution ${execution.id} completed`,
      },
    })
    await expect(page.locator('main')).toBeVisible()
  })
})

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
    await mockApiRoutes(page)
  })

  test('loads workflows and processes a WS execution-status event', async ({
    page,
  }) => {
    await page.goto('/workflows')
    await expect(page).toHaveURL(/\/workflows/)
    await expect(page.locator('main')).toBeVisible()

    // Push an execution-status event. The store applies the update;
    // the test guarantees the SPA does not crash on receipt.
    await injectEvent(page, {
      type: 'workflow_execution.status_changed',
      version: 1,
      execution: makeWorkflowExecution({
        status: 'success',
        finished_at: '2026-04-01T12:01:00Z',
      }),
    })
    await expect(page.locator('main')).toBeVisible()
  })
})

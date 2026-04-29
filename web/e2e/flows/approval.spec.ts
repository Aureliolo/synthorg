import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeApprovalRequest } from '../factories'

/**
 * Critical-flow E2E: approval workflow.
 *
 * Loads the approvals page, asserts the list mounts, and exercises
 * a server-pushed approval transition via the WebSocket harness.
 */

test.describe('Approval critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('loads the approvals page and accepts WS-pushed updates', async ({ page }) => {
    await page.goto('/approvals')
    await expect(page).toHaveURL(/\/approvals/)
    await expect(page.locator('main')).toBeVisible()

    // Inject a synthetic WebSocket event matching the wire shape for
    // an approval status transition. The dashboard's WS layer parses
    // the frame and updates the approval store; the assertion here is
    // the negative one -- the page does not crash on receipt -- which
    // is the minimum guarantee a regression like a missing event
    // type discriminator would break.
    const approval = makeApprovalRequest({ status: 'approved' })
    await injectEvent(page, {
      type: 'approval.status_changed',
      version: 1,
      approval,
    })
    await expect(page.locator('main')).toBeVisible()
  })
})

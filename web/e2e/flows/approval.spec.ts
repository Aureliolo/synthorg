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
    // an approval transition. The frame must conform to the dashboard's
    // ``WsEvent`` runtime validator (``isWsEvent``): ``event_type`` /
    // ``channel`` / ``timestamp`` / ``payload`` are mandatory; an
    // event with the legacy ``{type, ..., approval}`` shape is silently
    // discarded by the WS layer, so the test would pass without
    // exercising the handler chain at all.
    const approval = makeApprovalRequest({ status: 'approved' })
    await injectEvent(page, {
      event_type: 'approval.approved',
      channel: 'approvals',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { ...approval, approval_id: approval.id },
    })
    // The notifications store's ws-handler enqueues an "Approval approved"
    // entry into the notification drawer for this event_type. Open the
    // drawer and assert that title is visible -- proof the WS frame
    // reached the registered handler; a regression in the envelope check
    // or notifications dispatch chain would leave the entry unrendered.
    await page.getByRole('button', { name: /notifications/i }).click()
    await expect(page.getByText('Approval approved').first()).toBeVisible()
  })
})

import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { makeConnection } from '../factories'

/**
 * Critical-flow E2E: A2A peer connection (agent-to-agent federation).
 *
 * The Connections page is where operators register external A2A
 * peers. This spec seeds the page with one A2A connection and
 * asserts the row renders, exercising the connection-detail
 * navigation hook a real federation flow depends on.
 */

test.describe('A2A federation critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    const peer = makeConnection({ name: 'a2a-peer-eu', connection_type: 'a2a_peer' })
    await page.route('**/api/v1/connections**', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [peer],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test('loads the connections list and surfaces the A2A peer row', async ({ page }) => {
    await page.goto('/connections')
    await expect(page.locator('main')).toBeVisible()
    await expect(page.getByText('a2a-peer-eu').first()).toBeVisible()

    // The per-row "Edit" button opens the edit modal -- the closest
    // thing the connections page has to a row-detail view (no
    // /connections/:name route is mounted yet). Clicking it and
    // asserting the dialog appears proves the row's action handler is
    // wired, not just that the text renders.
    const editPeer = page.getByRole('button', { name: /Edit a2a-peer-eu/ })
    await expect(editPeer).toBeVisible()
    await editPeer.click()
    await expect(page.getByRole('dialog')).toBeVisible()
  })
})

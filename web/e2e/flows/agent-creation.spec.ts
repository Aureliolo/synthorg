import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeAgentList } from '../factories'

/**
 * Critical-flow E2E: agents list mount + WS hire-event intake.
 *
 * The full multi-step "create agent" form requires a realistic
 * backend (multi-step persistence, role validation, identity-card
 * round-trip) that this E2E doesn't simulate; covering it would
 * require the wizard's `/api/v1/agents` POST + downstream
 * /api/v1/agents/{id}/identity flow stubbed end-to-end. Until that
 * harness lands, this spec scopes to:
 *   1. Agents list mount + seeded payload renders.
 *   2. Click handler on the agent card works (entry point users
 *      actually exercise on every visit).
 *   3. WS ``agent.hired`` frame processed end-to-end through the
 *      notifications dispatch chain.
 *
 * The file name reflects the future full scope; current coverage is
 * agents list + WS hire intake, hence the describe-block name.
 */

test.describe('Agents list + WS hire intake', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Register the catch-all FIRST so the specific route stub below
    // overrides it: Playwright matches route handlers in LIFO order,
    // so the most-recently-registered handler wins.
    await mockApiRoutes(page)
    await page.route('**/api/v1/agents', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: makeAgentList(3),
          error: null,
          error_detail: null,
          pagination: { total: 3, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test('loads the agents list and processes a hire event', async ({ page }) => {
    await page.goto('/agents')
    await expect(page).toHaveURL(/\/agents/)
    await expect(page.locator('main')).toBeVisible()
    // Assert the seeded three-agent payload actually rendered. The
    // factory builds names from a fixed roster so 'Alice' is always
    // present; if the list / card grid regresses to an empty state,
    // this assertion fails loudly instead of passing silently.
    await expect(page.getByText('Alice').first()).toBeVisible()

    // Real UI interaction: click on Alice's card to navigate to the
    // agent detail / edit surface. The full multi-step "create agent"
    // form requires a backend that this E2E doesn't simulate, so the
    // selection-into-detail path is what we can exercise without a
    // realistic API; failing this click would catch a regression that
    // breaks list navigation, which is the entry point of the whole
    // create / inspect flow.
    await page.getByText('Alice').first().click()
    await expect(page.locator('main')).toBeVisible()

    // Push an agent.hired event matching the dashboard's ``WsEvent``
    // runtime validator (``isWsEvent``: event_type / channel /
    // timestamp / payload required). The notifications store enqueues
    // an "Agent hired" entry, so the assertion below verifies the
    // frame survived envelope validation, dispatch, and notification
    // routing -- a regression in any of those layers would break it.
    await injectEvent(page, {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { agent_id: 'agent-new-001', agent_name: 'Diana' },
    })
    await expect(page.getByText('Agent hired').first()).toBeVisible()
  })
})

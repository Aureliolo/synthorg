import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeAgentList, makeCompanyConfig, makeDepartment, makeOrgAgent } from '../factories'
import { clickButton, fillForm, selectOption } from '../helpers/interactions'

/**
 * Critical-flow E2E: agents list intake + the create-agent form.
 *
 * Two journeys, two surfaces:
 *   1. The ``/agents`` list mounts a seeded payload, exercises card
 *      navigation, and processes a WS ``agent.hired`` frame end-to-end
 *      through the notifications dispatch chain.
 *   2. The org-edit Agents tab (``/org/edit?tab=agents``) drives the
 *      actual create-agent form: open the dialog, fill name / role /
 *      department, submit, and assert the ``POST /agents`` round-trip
 *      closes the dialog and the new agent lands on the board (the store
 *      appends the created agent to ``config.agents``).
 */

test.describe('Agents list + WS hire intake', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Register the catch-all FIRST so the specific route stub below
    // overrides it: Playwright matches route handlers in LIFO order,
    // so the most-recently-registered handler wins.
    await mockApiRoutes(page)
    // Trailing ``**`` so the glob matches the paginated request
    // (``/agents?limit=50&offset=0``); without it the query-string URL
    // falls through to the empty catch-all and the list renders empty.
    await page.route('**/api/v1/agents**', (route) =>
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
    // agent detail / edit surface.
    await page.getByText('Alice').first().click()
    await expect(page.locator('main')).toBeVisible()

    // Push an agent.hired event matching the dashboard's ``WsEvent``
    // runtime validator (``isWsEvent``: event_type / channel / timestamp
    // / payload required). The notifications store's ws-handler enqueues
    // an "Agent hired" entry into the notification drawer.
    await injectEvent(page, {
      event_type: 'agent.hired',
      channel: 'agents',
      timestamp: '2026-04-01T12:00:00Z',
      payload: { agent_id: 'agent-new-001', agent_name: 'Diana' },
    })
    // The entry lives in the notification drawer (not a transient toast),
    // so open it to assert the frame survived envelope validation,
    // dispatch, and notification routing end-to-end.
    await clickButton(page, /notifications/i)
    await expect(page.getByText('Agent hired').first()).toBeVisible()
  })
})

test.describe('Agent creation form', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    // Seed a company with one department so the board renders (not the
    // empty state) and the dialog's department picker has an option.
    await page.route('**/api/v1/company', (route) => {
      if (route.request().method() !== 'GET') {
        route.fallback()
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: makeCompanyConfig({
            departments: [makeDepartment({ name: 'engineering', display_name: 'Engineering' })],
            agents: [],
          }),
          error: null,
          error_detail: null,
        },
      })
    })
  })

  test('creates an agent and lands it on the board', async ({ page }) => {
    // POST /agents echoes the submitted fields back as a full AgentConfig;
    // the store appends it to config.agents so the card appears.
    await page.route('**/api/v1/agents', (route) => {
      if (route.request().method() !== 'POST') {
        route.fallback()
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: makeOrgAgent({
            id: 'agent-grace-001',
            name: 'Grace',
            role: 'Backend Developer',
            department: 'engineering',
          }),
          error: null,
          error_detail: null,
        },
      })
    })

    await page.goto('/org/edit?tab=agents')
    await expect(page).toHaveURL(/\/org\/edit/)
    await expect(page.locator('main')).toBeVisible()

    await clickButton(page, /add agent/i)

    // The dialog mounts with its "New Agent" title and the create form.
    const dialogTitle = page.getByText('New Agent')
    await expect(dialogTitle).toBeVisible()

    await fillForm(page, { Name: 'Grace', Role: 'Backend Developer' })
    await selectOption(page, 'Department', 'Engineering')

    const [created] = await Promise.all([
      page.waitForResponse(
        (res) => res.url().includes('/api/v1/agents') && res.request().method() === 'POST',
      ),
      clickButton(page, /create agent/i),
    ])
    expect(created.request().method()).toBe('POST')

    // On success the dialog closes and the new agent renders on the board.
    await expect(dialogTitle).toHaveCount(0)
    await expect(page.getByText('Grace').first()).toBeVisible()
  })
})

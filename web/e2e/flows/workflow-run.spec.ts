import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeWorkflow, makeWorkflowExecution } from '../factories'
import { clickButton, clickLocator, fillForm } from '../helpers/interactions'

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
    await page.route('**/api/v1/workflows**', (route) =>
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

    // Real UI interaction: click on the seeded workflow row so the
    // selection / detail-open path is exercised before the WS event
    // arrives. The makeWorkflow factory seeds name='Daily standup';
    // a regression in the click handler or list-item rendering would
    // surface here. Mandatory: the list payload guarantees one row,
    // so the locator must match.
    const seededWorkflow = page.getByText('Daily standup').first()
    await expect(seededWorkflow).toBeVisible()
    await clickLocator(seededWorkflow)
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

  test('creates a workflow via the POST /workflows round-trip', async ({ page }) => {
    // Single source of truth for the name so the form input, the mocked
    // response, and the toast assertion cannot drift apart if the
    // makeWorkflow() factory defaults change.
    const workflowName = 'Daily standup'
    // POST-specific stub wins over the GET list route (Playwright LIFO).
    await page.route('**/api/v1/workflows', (route) => {
      if (route.request().method() !== 'POST') {
        route.fallback()
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: makeWorkflow({ name: workflowName }),
          error: null,
          error_detail: null,
        },
      })
    })

    await page.goto('/workflows')
    await expect(page).toHaveURL(/\/workflows/)

    // "New workflow" opens the create drawer; filling the name and
    // submitting drives the real POST round-trip the store owns.
    await clickButton(page, /new workflow/i)
    await fillForm(page, { Name: workflowName })

    const [created] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes('/api/v1/workflows') && res.request().method() === 'POST',
      ),
      clickButton(page, /create workflow/i),
    ])
    expect(created.request().method()).toBe('POST')

    // The store emits a success toast on the confirmed create; its title
    // carries the workflow name, proving the dispatch chain completed.
    await expect(
      page.getByText(new RegExp(`Workflow ${workflowName} created`, 'i')).first(),
    ).toBeVisible()
  })
})

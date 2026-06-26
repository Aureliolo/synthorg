import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeWorkflow, makeWorkflowExecution } from '../factories'
import { clickButton, clickLocator, fillForm } from '../helpers/interactions'

/**
 * Critical-flow E2E: workflows list mount + workflow-execution notification.
 *
 * Mounts the workflows list with a deterministic single-workflow payload and
 * pushes a ``workflow_execution.status_changed`` WebSocket frame on the
 * ``workflows`` channel. Asserts the notifications dispatch chain renders the
 * "Workflow execution failed" entry, exercising the full envelope-validate ->
 * dispatch -> notification-render path end-to-end against the real production
 * event type and notifications-store router.
 */

test.describe('Workflows list + workflow-execution notification path', () => {
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

  test('loads workflows and processes a workflow-execution failure notification', async ({
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

    // Push the real ``workflow_execution.status_changed`` event on the
    // ``workflows`` channel with a terminal ``failed`` status. The
    // notifications-store router maps it to a ``workflows.execution_failed``
    // entry titled "Workflow execution failed" with the execution id as the
    // description, exercising the full envelope-validate -> dispatch ->
    // notification-render chain against the production event type.
    const execution = makeWorkflowExecution({ status: 'failed' })
    await injectEvent(page, {
      event_type: 'workflow_execution.status_changed',
      channel: 'workflows',
      timestamp: '2026-04-01T12:01:00Z',
      payload: {
        execution_id: execution.id,
        definition_id: execution.workflow_id,
        status: 'failed',
        actor: 'operator',
      },
    })
    // Assert both the static title and the payload-driven execution id so a
    // regression that drops the description (or renders only the title) fails.
    await expect(page.getByText('Workflow execution failed').first()).toBeVisible()
    await expect(page.getByText(execution.id).first()).toBeVisible()
    await expect(page.locator('main')).toBeVisible()
  })

  test('activates and cancels a workflow execution round-trip', async ({ page }) => {
    const workflowId = 'workflow-001'
    const executionId = 'exec-77'
    // The list route reflects the cancellation once the POST flips this flag,
    // so the post-cancel reload shows the terminal ``cancelled`` status.
    let cancelled = false

    const makeExecution = () => ({
      id: executionId,
      definition_id: workflowId,
      definition_revision: 1,
      version: 1,
      status: cancelled ? 'cancelled' : 'running',
      activated_by: 'operator',
      project: 'default',
      created_at: '2026-04-01T12:00:00Z',
      updated_at: '2026-04-01T12:00:00Z',
      completed_at: cancelled ? '2026-04-01T12:05:00Z' : null,
      error: null,
      node_executions: [],
    })

    await page.route(
      `**/api/v1/workflow-executions/by-definition/${workflowId}**`,
      (route) =>
        route.fulfill({
          json: {
            success: true,
            data: [makeExecution()],
            error: null,
            error_detail: null,
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

    await page.route(
      `**/api/v1/workflow-executions/${executionId}/cancel`,
      (route) => {
        if (route.request().method() !== 'POST') {
          route.fallback()
          return
        }
        cancelled = true
        route.fulfill({
          json: {
            success: true,
            data: makeExecution(),
            error: null,
            error_detail: null,
          },
        })
      },
    )

    await page.goto(`/workflows/${workflowId}/executions`)
    await expect(page).toHaveURL(/\/executions/)
    // The activated (running) execution renders with its in-flight Cancel
    // affordance; a terminal status would hide the button.
    await expect(page.getByText('running').first()).toBeVisible()

    // Row Cancel opens the confirm dialog; ``Cancel run`` fires the
    // POST .../cancel round-trip the executions controller owns.
    await clickButton(page, /^cancel$/i)
    await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes(`/workflow-executions/${executionId}/cancel`)
          && res.request().method() === 'POST',
      ),
      clickButton(page, /cancel run/i),
    ])

    // The success toast confirms the round-trip, and the reloaded list now
    // reports the terminal ``cancelled`` status, proving the UI reflects the
    // activated execution's new state after the API exchange.
    await expect(page.getByText('Cancellation requested').first()).toBeVisible()
    await expect(page.getByText('cancelled').first()).toBeVisible()
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

import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeApprovalRequest } from '../factories'
import {
  makeKanbanColumn,
  makeTask,
  type MockTask,
  type TaskStatus,
} from '../factories/tasks'
import { clickLocator } from '../helpers/interactions'

/**
 * Critical-flow E2E: Kanban board mount + WS frame intake + click.
 *
 * Walks the user journey:
 *   1. Page mounts the Kanban board with deterministic columns and
 *      asserts the seeded todo task lands in the ``backlog`` column
 *      via ``data-column-id`` (the production board groups status
 *      ``created`` / ``ready`` / ``in_progress`` / ``in_review`` /
 *      ``done`` / ``blocked`` / ``terminal``; see ``KANBAN_COLUMNS``
 *      in ``web/src/utils/tasks.ts``).
 *   2. The user clicks the seeded task card (mandatory interaction).
 *   3. A high-risk approval request fires via WS (envelope-validated).
 *   4. Reviewer approves via WS (envelope-validated).
 *   5. WS task.status_changed frames stream through the dashboard.
 *
 * Why no in-flight column-membership assertions for the WS task
 * transitions: the production tasks store does NOT subscribe to
 * ``task.status_changed`` for column re-routing (only the
 * notifications store consumes it, and only for ``failed`` /
 * ``blocked`` statuses; see ``web/src/stores/notifications.ts``).
 * Asserting column membership after each WS frame would always fail
 * because the seeded task stays where the API placed it. The frames
 * still get value-asserted -- a regression that crashes the WS
 * handler chain or fails ``isWsEvent`` envelope validation would
 * tear the page down and the final ``main visible`` + title assertion
 * would fail.
 *
 * The drag-drop column move is not exercised end-to-end here -- the
 * Kanban DnD path requires the page's HTML5 drag handlers, which is
 * a separate flow.
 *
 * Wire envelope: events match the dashboard's ``WsEvent`` runtime
 * validator (``isWsEvent`` in ``stores/websocket.ts``); the legacy
 * ``{type, ...domain}`` shape is silently discarded by the WS layer.
 */

test.describe('Task lifecycle critical flow', () => {
  // Seed the Kanban board with one task per column so the dashboard's
  // grouping logic has rows to render. Use production status values
  // (``created``, ``in_progress``, ...) so each seeded task lands in
  // the Kanban column defined by ``STATUS_TO_COLUMN`` in
  // ``web/src/utils/tasks.ts``. Hoisted to describe scope so the test
  // body can reference ``allTasks[0]`` without crossing the
  // ``beforeEach`` closure boundary (lifting fixes a ReferenceError
  // that fired when the suite was actually executed).
  const columns: Record<TaskStatus, MockTask[]> = {
    created: makeKanbanColumn('created', 1),
    assigned: makeKanbanColumn('assigned', 0),
    in_progress: makeKanbanColumn('in_progress', 1),
    in_review: makeKanbanColumn('in_review', 0),
    blocked: makeKanbanColumn('blocked', 0),
    auth_required: makeKanbanColumn('auth_required', 0),
    completed: makeKanbanColumn('completed', 0),
    failed: makeKanbanColumn('failed', 0),
    interrupted: makeKanbanColumn('interrupted', 0),
    cancelled: makeKanbanColumn('cancelled', 0),
    rejected: makeKanbanColumn('rejected', 0),
    suspended: makeKanbanColumn('suspended', 0),
  }
  const allTasks = Object.values(columns).flat()

  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    // Catch-all FIRST so the specific stub below wins (Playwright
    // matches handlers LIFO).
    await mockApiRoutes(page)
    await page.route('**/api/v1/tasks**', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: allTasks,
          error: null,
          error_detail: null,
          pagination: { total: allTasks.length, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test(
    'mounts Kanban, asserts backlog column, processes WS approval + status frames',
    async ({ page }) => {
      await page.goto('/tasks')
      await expect(page).toHaveURL(/\/tasks/)
      await expect(page.locator('main')).toBeVisible()

      // Real UI interaction: click on the seeded backlog task so the
      // selection / detail-open path is exercised in addition to the
      // synthetic WS frames below. Kanban DnD requires the page's
      // HTML5 drag handlers (a separate flow); the card click is the
      // entry point users exercise on every visit, so a regression
      // in the click handler surfaces here. Mandatory: the seed
      // builds one ``created``-status task that ``STATUS_TO_COLUMN``
      // maps to the ``backlog`` column; the locator must match.
      const seededTask = allTasks[0]
      const backlogColumn = page.locator('[data-column-id="backlog"]')
      await expect(backlogColumn).toBeVisible()
      const todoCard = backlogColumn.getByText(seededTask.title).first()
      await expect(todoCard).toBeVisible()
      // Use the shared ``clickLocator`` helper instead of a direct
      // ``locator.click()`` so every flow spec runs through the same
      // wait-for-visibility-then-click sequence; Tier-1 specs avoid
      // bare Playwright primitives in favour of the helpers in
      // ``web/e2e/helpers/interactions.ts``.
      await clickLocator(todoCard)
      await expect(page.locator('main')).toBeVisible()

      // Step 1: a high-risk task gates on approval.
      const highRiskTask = makeTask({
        id: 'task-high-risk',
        title: 'High-risk migration',
        priority: 'critical',
        approved: false,
      })
      const approval = makeApprovalRequest({
        id: 'approval-high-risk',
        title: 'Approve high-risk migration',
        task_id: highRiskTask.id,
        risk_tier: 'high',
        status: 'pending',
      })
      await injectEvent(page, {
        event_type: 'approval.submitted',
        channel: 'approvals',
        timestamp: '2026-04-01T12:00:00Z',
        payload: {
          ...approval,
          approval_id: approval.id,
          task: highRiskTask,
        },
      })

      // Step 2: reviewer approves.
      await injectEvent(page, {
        event_type: 'approval.approved',
        channel: 'approvals',
        timestamp: '2026-04-01T12:01:00Z',
        payload: {
          ...approval,
          approval_id: approval.id,
          status: 'approved',
          reviewer_id: 'agent-002',
        },
      })

      // Step 3: task transitions TODO -> IN_PROGRESS via the
      // server-pushed status event.
      await injectEvent(page, {
        event_type: 'task.status_changed',
        channel: 'tasks',
        timestamp: '2026-04-01T12:02:00Z',
        payload: {
          ...highRiskTask,
          task_id: highRiskTask.id,
          approved: true,
          status: 'in_progress',
        },
      })

      // Step 4: task transitions IN_PROGRESS to COMPLETED. Production
      // TaskStatus uses ``completed`` (not ``done``); STATUS_TO_COLUMN
      // maps ``completed`` to the ``done`` Kanban column. Sending the
      // canonical wire value keeps the event compatible with the
      // production ws layer if/when a handler is wired up later.
      await injectEvent(page, {
        event_type: 'task.status_changed',
        channel: 'tasks',
        timestamp: '2026-04-01T12:03:00Z',
        payload: {
          ...highRiskTask,
          task_id: highRiskTask.id,
          approved: true,
          status: 'completed',
        },
      })

      // After the four WS frames, the seeded backlog task must still
      // be visible in the ``backlog`` column. The test does NOT
      // assert column transition for the WS-injected high-risk task
      // because the production tasks store does not subscribe to
      // ``task.status_changed`` for column re-routing (only the
      // notifications store consumes it, and only for ``failed`` /
      // ``blocked`` statuses; see the docstring at the top of this
      // file). The assertions below catch the regressions a Tier-1
      // flow needs to catch: any event throwing in the WS dispatch
      // chain or failing ``isWsEvent`` would tear the page down or
      // drop the seeded task from the rendered DOM.
      await expect(backlogColumn.getByText(seededTask.title).first()).toBeVisible()
      await expect(page.locator('main')).toBeVisible()
    },
  )
})

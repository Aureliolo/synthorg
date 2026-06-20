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

  test('approves a pending request via the POST .../approve round-trip', async ({ page }) => {
    // Full wire shape: ``ApprovalResponse`` carries fields the e2e
    // factory omits (risk_level, action_type, ...). A pending status is
    // what renders the card's inline Approve / Reject buttons.
    const pending = {
      ...makeApprovalRequest({ status: 'pending' }),
      risk_level: 'medium',
      action_type: 'deploy',
      requested_by: 'agent-001',
      source: 'review_gate',
      urgency_level: 'medium',
      metadata: {},
      seconds_remaining: null,
      expires_at: null,
      consumed_at: null,
      decided_at: null,
      decided_by: null,
      decision_reason: null,
      evidence_package: null,
    }
    await page.route('**/api/v1/approvals**', (route) => {
      if (route.request().method() !== 'GET') {
        route.fallback()
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: [pending],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50, next_cursor: null, has_more: false },
        },
      })
    })
    await page.route(`**/api/v1/approvals/${pending.id}/approve`, (route) =>
      route.fulfill({
        json: {
          success: true,
          data: { ...pending, status: 'approved' },
          error: null,
          error_detail: null,
        },
      }),
    )

    await page.goto('/approvals')
    await expect(page.getByText('Deploy to production').first()).toBeVisible()

    const [decided] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes(`/api/v1/approvals/${pending.id}/approve`) &&
          res.request().method() === 'POST',
      ),
      page.getByRole('button', { name: /^approve$/i }).first().click(),
    ])
    expect(decided.request().method()).toBe('POST')

    // The store emits a success toast on the confirmed decision.
    await expect(page.getByText('Approval granted').first()).toBeVisible()
  })

  test('rejects a pending request via the drawer + POST .../reject round-trip', async ({
    page,
  }) => {
    const pending = {
      ...makeApprovalRequest({ status: 'pending' }),
      risk_level: 'medium',
      action_type: 'deploy',
      requested_by: 'agent-001',
      source: 'review_gate',
      urgency_level: 'medium',
      metadata: {},
      seconds_remaining: null,
      expires_at: null,
      consumed_at: null,
      decided_at: null,
      decided_by: null,
      decision_reason: null,
      evidence_package: null,
    }
    await page.route('**/api/v1/approvals**', (route) => {
      if (route.request().method() !== 'GET') {
        route.fallback()
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: [pending],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50, next_cursor: null, has_more: false },
        },
      })
    })
    // Detail GET feeds the drawer a single approval (not a list), so its
    // pending footer (Approve / Reject) renders.
    await page.route(`**/api/v1/approvals/${pending.id}`, (route) => {
      if (route.request().method() !== 'GET') {
        route.fallback()
        return
      }
      route.fulfill({
        json: { success: true, data: pending, error: null, error_detail: null },
      })
    })
    await page.route(`**/api/v1/approvals/${pending.id}/reject`, (route) =>
      route.fulfill({
        json: {
          success: true,
          data: { ...pending, status: 'rejected' },
          error: null,
          error_detail: null,
        },
      }),
    )

    // The card's Reject opens the detail drawer (role=dialog); the
    // drawer's Reject opens an alert-dialog requiring a reason.
    await page.goto('/approvals')
    await expect(page.getByText('Deploy to production').first()).toBeVisible()
    await page.getByRole('button', { name: /^reject$/i }).first().click()

    const drawer = page.getByRole('dialog')
    await expect(drawer).toBeVisible()
    // Drawer footer Reject -> opens the "Reject Action" alert-dialog.
    await drawer.getByRole('button', { name: /^reject$/i }).first().click()
    const confirm = page.getByRole('alertdialog')
    await expect(confirm.getByText(/reject action/i)).toBeVisible()
    await confirm.getByRole('textbox').fill('Not authorised for production')

    const [decided] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes(`/api/v1/approvals/${pending.id}/reject`) &&
          res.request().method() === 'POST',
      ),
      confirm.getByRole('button', { name: /^reject$/i }).click(),
    ])
    expect(decided.request().method()).toBe('POST')
    await expect(page.getByText('Approval rejected').first()).toBeVisible()
  })
})

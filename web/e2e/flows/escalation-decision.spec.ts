import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'

/**
 * Critical-flow E2E: escalation queue decision.
 *
 * A conflict the autonomous resolvers cannot decide lands in the
 * escalation queue for human review. This flow drives the full review
 * round-trip: the pending escalation renders as a card, "Review" opens the
 * detail drawer, the operator fills the reasoning and submits, and the
 * dashboard fires the ``POST .../decision`` the escalations store owns. A
 * regression in the queue fetch, the drawer detail load, or the decision
 * submit would silently strand human-in-the-loop governance.
 */

function ok(data: unknown) {
  return { success: true, data, error: null, error_detail: null }
}

function page1(data: unknown[]) {
  return {
    success: true,
    data,
    error: null,
    error_detail: null,
    pagination: { total: data.length, offset: 0, limit: 50, next_cursor: null, has_more: false },
  }
}

const ESCALATION = {
  escalation: {
    id: 'esc-1',
    conflict: {
      id: 'conflict-1',
      type: 'resource',
      task_id: null,
      subject: 'Disputed sprint allocation',
      positions: [
        {
          agent_id: 'agent-a',
          agent_department: 'Engineering',
          agent_level: 'senior',
          position: 'Prioritise the payments migration',
          reasoning: 'Revenue risk is highest there.',
          timestamp: '2026-04-19T00:00:00Z',
        },
        {
          agent_id: 'agent-b',
          agent_department: 'Engineering',
          agent_level: 'lead',
          position: 'Prioritise the onboarding rewrite',
          reasoning: 'Churn is climbing this quarter.',
          timestamp: '2026-04-19T00:00:00Z',
        },
      ],
      detected_at: '2026-04-19T00:00:00Z',
      is_cross_department: false,
    },
    status: 'pending',
    created_at: '2026-04-19T00:00:00Z',
    expires_at: null,
    decided_at: null,
    decided_by: null,
    decision: null,
  },
  conflict_id: 'conflict-1',
  status: 'pending',
}

test.describe('Escalation queue decision', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
  })

  test('reviews a pending escalation and submits a decision', async ({ page }) => {
    await page.route(/\/api\/v1\/conflicts\/escalations(\?.*)?$/, (route) =>
      route.fulfill({ json: page1([ESCALATION]) }),
    )
    await page.route('**/api/v1/conflicts/escalations/esc-1', (route) =>
      route.fulfill({ json: ok(ESCALATION) }),
    )
    await page.route('**/api/v1/conflicts/escalations/esc-1/decision', (route) =>
      route.fulfill({
        json: ok({
          ...ESCALATION,
          status: 'decided',
          escalation: {
            ...ESCALATION.escalation,
            status: 'decided',
            decided_at: '2026-04-19T01:00:00Z',
            decided_by: 'operator',
          },
        }),
      }),
    )

    await page.goto('/conflicts/escalations')

    // The pending escalation renders as a card with its conflict subject.
    await expect(page.getByText('Disputed sprint allocation')).toBeVisible()

    // Review opens the detail drawer (fetches the single escalation).
    await page.getByRole('button', { name: 'Review' }).click()
    const reasoning = page.getByLabel('Reasoning')
    await expect(reasoning).toBeVisible()
    await reasoning.fill('Payments migration carries the larger revenue risk.')

    // Submitting fires the decision round-trip the store owns.
    const [decision] = await Promise.all([
      page.waitForResponse(
        (res) =>
          /\/conflicts\/escalations\/esc-1\/decision$/.test(res.url()) &&
          res.request().method() === 'POST',
      ),
      page.getByRole('button', { name: 'Submit' }).click(),
    ])
    expect(decision.request().method()).toBe('POST')
  })
})

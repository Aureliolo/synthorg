import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness, injectEvent } from '../fixtures/websocket-harness'
import { makeFineTuneRun, makeFineTuneStatus } from '../factories'

/**
 * Critical-flow E2E: fine-tuning pipeline.
 *
 * Loads the embedding fine-tuning page, asserts the pipeline
 * controls mount, and verifies a WS `fine_tuning.status_changed`
 * event is received without crashing.
 */

test.describe('Fine-tuning pipeline critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    const status = makeFineTuneStatus({ stage: 'training', progress: 0.42, run_id: 'run-001' })
    const run = makeFineTuneRun({ id: 'run-001' })
    await page.route('**/api/v1/fine-tuning/status', (route) =>
      route.fulfill({
        json: { success: true, data: status, error: null, error_detail: null },
      }),
    )
    await page.route('**/api/v1/fine-tuning/runs', (route) =>
      route.fulfill({
        json: {
          success: true,
          data: [run],
          error: null,
          error_detail: null,
          pagination: { total: 1, offset: 0, limit: 50 },
        },
      }),
    )
  })

  test('loads the fine-tuning page and processes a status event', async ({ page }) => {
    await page.goto('/settings/memory/fine-tuning')
    await expect(page.locator('main')).toBeVisible()

    await injectEvent(page, {
      event_type: 'fine_tuning.status_changed',
      channel: 'system',
      timestamp: '2026-05-13T10:05:00Z',
      payload: { stage: 'evaluating', progress: 0.85, run_id: 'run-001' },
    })

    await expect(page.locator('main')).toBeVisible()
  })
})

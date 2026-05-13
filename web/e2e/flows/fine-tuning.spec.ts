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

  test('loads the fine-tuning page and processes a stage_changed WS event', async ({ page }) => {
    await page.goto('/settings/memory/fine-tuning')
    await expect(page.locator('main')).toBeVisible()
    // The initial API mock returns ``stage: 'training'``; the
    // pipeline progress bar therefore shows "Stage: Training"
    // before the WS event flips the store to ``evaluating``.
    await expect(page.getByText('Stage: Training').first()).toBeVisible()

    // Use the real wire event type ``memory.fine_tune.stage_changed``;
    // the store handler in web/src/stores/fine-tuning.ts (~line 252)
    // narrows on it and resets ``progress: 0`` while applying the
    // new stage, which is observable as the progress-bar label flip.
    await injectEvent(page, {
      event_type: 'memory.fine_tune.stage_changed',
      channel: 'system',
      timestamp: '2026-05-13T10:05:00Z',
      payload: { run_id: 'run-001', stage: 'evaluating', previous_stage: 'training' },
    })

    await expect(page.getByText('Stage: Evaluation').first()).toBeVisible()
  })
})

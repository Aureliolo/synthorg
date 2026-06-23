import { test, expect, type Page } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'

/**
 * Critical-flow E2E: setup wizard agents step.
 *
 * After a template generates the org's agents, the guided wizard's Agents
 * step lets the operator rename, re-model, and re-personalise each one
 * before the company goes live. This flow drives the rename round-trip:
 * the agent card renders, the operator edits the inline name field, and
 * "save" fires the ``PUT /setup/agents/{index}/name`` the wizard store
 * owns. A regression in the agents fetch, the card render, or the update
 * wiring would strand the operator with an uneditable roster.
 */

/** Wrap a payload in the dashboard's ``ApiResponse`` success envelope. */
function ok(data: unknown) {
  return { success: true, data, error: null, error_detail: null }
}

/** Wrap a list in the dashboard's cursor-paginated success envelope. */
function page1(data: unknown[]) {
  return {
    success: true,
    data,
    error: null,
    error_detail: null,
    pagination: { total: data.length, offset: 0, limit: 50, next_cursor: null, has_more: false },
  }
}

const AGENT = {
  name: 'Ada',
  role: 'Engineer',
  department: 'Engineering',
  level: 'senior',
  model_provider: null,
  model_id: null,
  personality_preset: null,
  tier: 'medium',
}

/**
 * Seed the persisted wizard store so a guided-mode wizard rehydrates on
 * the Agents step with every prior step complete. ``providers`` and
 * ``agents`` are NOT part of the store's own ``partialize`` set, but the
 * rehydration merge reads any key present in the persisted ``state`` -- so
 * seeding non-empty maps here defeats the ``unmark*IfEmptyRehydration``
 * guards that would otherwise re-block the Providers / Agents steps (and
 * bounce the URL back to the first incomplete step).
 */
async function seedWizardOnAgentsStep(page: Page): Promise<void> {
  await page.addInitScript((agent) => {
    window.localStorage.setItem(
      'synthorg-setup-wizard-v1',
      JSON.stringify({
        version: 3,
        state: {
          currentStep: 'agents',
          wizardMode: 'guided',
          stepsCompleted: {
            account: false,
            mode: true,
            template: true,
            providers: true,
            company: true,
            agents: true,
            theme: false,
            complete: false,
          },
          providers: {
            'example-provider': { name: 'example-provider', enabled: true, models: [] },
          },
          agents: [agent],
        },
      }),
    )
  }, AGENT)
}

test.describe('Setup wizard agents step', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await seedWizardOnAgentsStep(page)
    await mockApiRoutes(page)
    // Unauthenticated: SetupCompleteGuard passes guests straight through
    // to the wizard without consulting setup status.
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({
        status: 401,
        json: { success: false, data: null, error: 'Not authenticated', error_detail: null },
      }),
    )
    // The Agents step re-fetches its roster on mount and loads the
    // personality presets; both back the card render under test.
    await page.route(/\/api\/v1\/setup\/agents(\?.*)?$/, (route) =>
      route.fulfill({ json: page1([AGENT]) }),
    )
    await page.route(/\/api\/v1\/setup\/personality-presets(\?.*)?$/, (route) =>
      route.fulfill({ json: page1([]) }),
    )
  })

  test('renames an agent via the PUT round-trip', async ({ page }) => {
    await page.route('**/api/v1/setup/agents/0/name', (route) =>
      route.fulfill({ json: ok({ ...AGENT, name: 'Ada Lovelace' }) }),
    )

    await page.goto('/setup/agents')

    // The roster renders the seeded agent's card.
    await expect(page.getByRole('heading', { name: /customize your agents/i })).toBeVisible()
    const editName = page.getByRole('button', { name: 'Edit: Ada' })
    await expect(editName).toBeVisible()

    // Entering edit mode focuses + selects the inline input; typing
    // replaces the selection, and Enter fires the PUT the store owns.
    await editName.click()
    await page.keyboard.type('Ada Lovelace')
    const [updated] = await Promise.all([
      page.waitForResponse(
        (res) =>
          /\/setup\/agents\/0\/name$/.test(res.url()) && res.request().method() === 'PUT',
      ),
      page.keyboard.press('Enter'),
    ])
    expect(updated.request().method()).toBe('PUT')
  })
})

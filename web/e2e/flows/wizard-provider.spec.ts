import { test, expect, type Page } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'

/**
 * Critical-flow E2E: setup wizard provider configuration.
 *
 * Drives the "Configure manually" path of the wizard's Providers step:
 * the custom-config modal opens with ``selectedPreset`` auto-set to
 * ``__custom__`` (so the form reveals immediately), the operator fills the
 * name / base-URL / API-key fields, and "Create Provider" fires the
 * ``POST /providers`` round-trip the wizard store owns. This is the first
 * provider the company gets, so a regression in the modal-reveal, the
 * submit gate, or the create wiring would strand the wizard before agents
 * can be hired.
 */

/** Wrap a payload in the dashboard's ``ApiResponse`` success envelope. */
function ok(data: unknown) {
  return { success: true, data, error: null, error_detail: null }
}

/**
 * Seed the wizard's persisted store so a quick-mode wizard rehydrates on
 * the Providers step with only the mode prerequisite complete. This is the
 * exact shape the persist middleware writes (``{ state, version }``,
 * version 3); the unlisted ``providers`` step stays incomplete so the step
 * renders its picker rather than a "done" summary.
 */
async function seedWizardOnProvidersStep(page: Page): Promise<void> {
  await page.addInitScript(() => {
    window.localStorage.setItem(
      'synthorg-setup-wizard-v1',
      JSON.stringify({
        version: 3,
        state: {
          currentStep: 'providers',
          wizardMode: 'quick',
          stepsCompleted: {
            account: false,
            mode: true,
            template: false,
            company: false,
            providers: false,
            agents: false,
            theme: false,
            complete: false,
          },
        },
      }),
    )
  })
}

test.describe('Setup wizard provider configuration', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await seedWizardOnProvidersStep(page)
    await mockApiRoutes(page)
    // Unauthenticated: SetupCompleteGuard passes guests straight through
    // to the wizard without consulting setup status.
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({
        status: 401,
        json: { success: false, data: null, error: 'Not authenticated', error_detail: null },
      }),
    )
    // No presets and no detected local servers: the step falls back to the
    // manual-configuration affordance, which is the path under test.
    await page.route('**/api/v1/providers/presets', (route) => route.fulfill({ json: ok([]) }))
    await page.route('**/api/v1/providers/probe-local', (route) =>
      route.fulfill({ json: ok({ detected: [], errors: {} }) }),
    )
  })

  test('configures a custom provider via the POST /providers round-trip', async ({ page }) => {
    // One regex handler owns both the list read (GET) and the create
    // (POST); the trailing ``(\?.*)?$`` keeps it off ``/providers/presets``
    // and ``/providers/probe-local`` (those need a ``/`` after the segment).
    const providerCreated = {
      ...ok({
        name: 'my-provider',
        enabled: true,
        auth_type: 'api_key',
        has_api_key: true,
        base_url: 'https://api.example.com/v1',
        custom_header_name: null,
        litellm_provider: null,
        oauth_client_id: null,
        oauth_scope: null,
        oauth_token_url: null,
        preset_name: null,
        tos_accepted_at: null,
        models: [],
        rate_limit: { requests_per_minute: 60, concurrent_requests: 10 },
      }),
    }
    await page.route(/\/api\/v1\/providers(\?.*)?$/, (route) => {
      if (route.request().method() === 'POST') {
        route.fulfill({ json: providerCreated })
        return
      }
      route.fulfill({
        json: {
          success: true,
          data: [],
          error: null,
          error_detail: null,
          pagination: { total: 0, offset: 0, limit: 50, next_cursor: null, has_more: false },
        },
      })
    })

    await page.goto('/setup/providers')
    await expect(page.getByRole('heading', { name: /set up providers/i })).toBeVisible()

    // The manual-config entry opens the provider form modal. In custom
    // create mode the form auto-selects ``__custom__`` on mount, so the
    // name / base-URL / API-key fields render without touching the preset
    // switcher.
    await page.getByRole('button', { name: /configure manually/i }).click()
    await page.getByLabel('Provider Name').fill('my-provider')
    await page.getByLabel('Base URL').fill('https://api.example.com/v1')
    await page.getByLabel('API Key').fill('sk-test-12345')

    // Filling the three required fields satisfies the submit gate; clicking
    // "Create Provider" drives the POST the wizard store owns.
    const [created] = await Promise.all([
      page.waitForResponse(
        (res) =>
          /\/api\/v1\/providers(\?.*)?$/.test(res.url()) && res.request().method() === 'POST',
      ),
      page.getByRole('button', { name: /^Create Provider$/ }).click(),
    ])
    expect(created.request().method()).toBe('POST')
  })
})

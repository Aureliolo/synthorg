import { test, expect, type Page } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { clickButton, fillForm } from '../helpers/interactions'

/**
 * Critical-flow E2E: setup wizard.
 *
 * The first describe block verifies the wizard root mounts and renders
 * its first-step heading and the URL-to-store deep-link sync. The second
 * drives a real submit: the company-creation step's ``POST /setup/company``
 * round-trip and its result preview. The wizard's persisted store
 * (localStorage, key ``synthorg-setup-wizard-v1``) is seeded so the
 * quick-mode flow lands on the company step with its prerequisites
 * (mode + providers) already complete. Because the store clears the
 * Providers completion flag when the non-persisted ``providers`` map
 * rehydrates empty, the seed also injects a live map (mirroring an
 * in-session provider creation) -- no internal API is poked.
 */

test.describe('Setup wizard critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    // Unauthenticated: SetupCompleteGuard passes guests straight through to
    // the wizard. Without this the catch-all ``/auth/me`` would read as an
    // authenticated, setup-complete session and redirect to the dashboard.
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ status: 401, json: { success: false, data: null, error: 'Not authenticated', error_detail: null } }),
    )
  })

  test('redirects the wizard root to the first step and renders it', async ({ page }) => {
    // ``/setup`` has no step segment, so WizardShell's URL sync redirects
    // to the first step (``mode``) and renders the mode-selection screen.
    await page.goto('/setup')
    await expect(page).toHaveURL(/\/setup\/mode/)
    await expect(
      page.getByRole('heading', { name: /how would you like to set up/i }),
    ).toBeVisible()
    // The two mode options are the wizard's entry affordance: a
    // radiogroup of role="radio" options labelled by their headings.
    await expect(page.getByRole('radio', { name: /guided setup/i })).toBeVisible()
    await expect(page.getByRole('radio', { name: /quick setup/i })).toBeVisible()
  })

  test('deep-links to a setup step and renders the step heading', async ({ page }) => {
    // Direct-URL navigation to `/setup/<step>` exercises the
    // WizardShell's URL-to-store sync path. A regression in
    // `canNavigateTo` or the redirect-to-first-incomplete logic
    // would either crash the page or punt us back to the first
    // step; either case fails the heading assertion below.
    await page.goto('/setup/mode')
    await expect(page).toHaveURL(/\/setup\/mode/)
    await expect(
      page.getByRole('heading', { name: /how would you like to set up/i }),
    ).toBeVisible()
  })
})

/** Wrap a payload in the dashboard's ``ApiResponse`` success envelope. */
function ok(data: unknown) {
  return { success: true, data, error: null, error_detail: null }
}

/**
 * A non-empty ``providers`` map mirroring the in-memory state after a user
 * configures a provider. ``providers`` is NOT persisted by the store's
 * ``partialize``, but the persist ``merge`` spreads the rehydrated payload
 * over the slice defaults, so injecting it here populates the live map the
 * same way an in-session provider creation would. ``mergePersistedSetupState``
 * clears ``stepsCompleted.providers`` (and gates downstream steps) whenever the
 * rehydrated map is empty, so seeding ``stepsCompleted.providers: true`` alone
 * is no longer enough to pass ``canNavigateTo`` -- a live map must accompany
 * it. The fields are the ones ``SetupSummary`` reads to render the connected
 * provider (``models``, ``auth_type``, ``has_api_key``).
 */
const SEEDED_PROVIDERS = {
  'example-provider': {
    name: 'example-provider',
    auth_type: 'api_key',
    has_api_key: true,
    has_oauth_credentials: false,
    has_custom_header: false,
    has_subscription_token: false,
    models: [],
  },
}

/**
 * Seed the wizard's persisted store so a quick-mode wizard rehydrates on
 * the company step with mode + providers already complete. This is the
 * exact shape the persist middleware writes (``{ state, version }``,
 * version 3); ``buildStepsCompleted`` overlays only ``true`` entries, so
 * the unlisted steps stay incomplete. The non-empty ``providers`` map keeps
 * ``stepsCompleted.providers`` from being cleared on rehydration (see
 * {@link SEEDED_PROVIDERS}), which is what lets ``canNavigateTo('company')``
 * pass without driving the provider picker.
 */
async function seedWizardOnCompanyStep(page: Page): Promise<void> {
  await page.addInitScript((providers) => {
    window.localStorage.setItem(
      'synthorg-setup-wizard-v1',
      JSON.stringify({
        version: 3,
        state: {
          currentStep: 'company',
          wizardMode: 'quick',
          providers,
          stepsCompleted: {
            account: false,
            mode: true,
            template: false,
            company: false,
            providers: true,
            agents: false,
            theme: false,
            complete: false,
          },
        },
      }),
    )
  }, SEEDED_PROVIDERS)
}

test.describe('Setup wizard company submit', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await seedWizardOnCompanyStep(page)
    await mockApiRoutes(page)
    // Unauthenticated: SetupCompleteGuard passes guests straight through
    // to the wizard without consulting setup status.
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ status: 401, json: { success: false, data: null, error: 'Not authenticated', error_detail: null } }),
    )
  })

  test('submits the company step and renders the generated preview', async ({ page }) => {
    await page.route('**/api/v1/setup/company', (route) => {
      if (route.request().method() !== 'POST') {
        route.fallback()
        return
      }
      route.fulfill({
        json: ok({
          company_name: 'E2E Test Co',
          description: null,
          template_applied: null,
          department_count: 1,
          agent_count: 1,
          agents: [{ name: 'Ada', department: 'engineering', tier: 'medium' }],
        }),
      })
    })

    await page.goto('/setup/company')
    await expect(page).toHaveURL(/\/setup\/company/)
    await expect(page.getByRole('heading', { name: /configure your company/i })).toBeVisible()

    await fillForm(page, { 'Company Name': 'E2E Test Co' })

    const [submitted] = await Promise.all([
      page.waitForResponse(
        (res) => res.url().includes('/api/v1/setup/company') && res.request().method() === 'POST',
      ),
      // Deep-linking straight to the company step selects no template, so
      // the button renders "Apply Default Template"; the optional group
      // also matches the plain "Apply Template" / "Re-apply Template" labels.
      clickButton(page, /apply (default )?template/i),
    ])
    expect(submitted.request().method()).toBe('POST')

    // The company response drives the preview: the Departments / Agents
    // metric cards and the generated-agent row only render once
    // ``companyResponse`` is set, so they confirm the submit landed.
    await expect(page.getByText('Departments').first()).toBeVisible()
    await expect(page.getByText('Agents').first()).toBeVisible()
    await expect(page.getByText(/Ada \(engineering\)/).first()).toBeVisible()
  })
})

/**
 * Seed the wizard's persisted store on the FINAL ``complete`` step with
 * every prerequisite quick-mode step done and a non-null
 * ``companyResponse`` (without which ``CompleteStep`` renders the
 * skip-wizard fallback instead of the review + complete UI). The non-empty
 * ``providers`` map keeps ``stepsCompleted.providers`` from being cleared on
 * rehydration (see {@link SEEDED_PROVIDERS}); without it the merge would gate
 * the Complete step and snap the wizard back to Providers.
 */
async function seedWizardOnCompleteStep(page: Page): Promise<void> {
  await page.addInitScript((providers) => {
    window.localStorage.setItem(
      'synthorg-setup-wizard-v1',
      JSON.stringify({
        version: 3,
        state: {
          currentStep: 'complete',
          wizardMode: 'quick',
          providers,
          stepsCompleted: {
            account: false,
            mode: true,
            template: false,
            company: true,
            providers: true,
            agents: false,
            theme: false,
            complete: false,
          },
          companyResponse: {
            company_name: 'E2E Test Co',
            description: null,
            template_applied: null,
            department_count: 1,
            agent_count: 1,
            agents: [{ name: 'Ada', department: 'engineering', tier: 'medium' }],
          },
        },
      }),
    )
  }, SEEDED_PROVIDERS)
}

test.describe('Setup wizard complete step', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await seedWizardOnCompleteStep(page)
    await mockApiRoutes(page)
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ status: 401, json: { success: false, data: null, error: 'Not authenticated', error_detail: null } }),
    )
  })

  test('completes setup and fires the POST /setup/complete round-trip', async ({ page }) => {
    await page.route('**/api/v1/setup/complete', (route) => {
      if (route.request().method() !== 'POST') {
        route.fallback()
        return
      }
      route.fulfill({
        json: ok({
          setup_complete: true,
          embedder_selected: true,
          embedder_failure_reason: null,
        }),
      })
    })

    await page.goto('/setup/complete')
    await expect(page).toHaveURL(/\/setup\/complete/)

    // "Complete Setup" opens the launch confirmation; "Launch" fires the
    // POST. Asserting the round-trip lands (not just the click) proves the
    // completion store action reached the endpoint.
    await clickButton(page, /complete setup/i)
    const [completed] = await Promise.all([
      page.waitForResponse(
        (res) =>
          res.url().includes('/api/v1/setup/complete') &&
          res.request().method() === 'POST',
      ),
      clickButton(page, /^launch$/i),
    ])
    expect(completed.request().method()).toBe('POST')
    // On success the wizard navigates away from the complete step.
    await expect(page).not.toHaveURL(/\/setup\/complete/)
  })

  test('renders an error banner when POST /setup/complete fails', async ({ page }) => {
    await page.route('**/api/v1/setup/complete', (route) => {
      if (route.request().method() !== 'POST') {
        route.fallback()
        return
      }
      route.fulfill({
        status: 500,
        json: { success: false, data: null, error: 'Internal error', error_detail: null },
      })
    })

    await page.goto('/setup/complete')
    await clickButton(page, /complete setup/i)
    await clickButton(page, /^launch$/i)

    // The completion store sets ``completionError``; CompleteStep renders
    // an error banner and keeps the operator on the step to retry.
    await expect(page.getByText(/could not complete setup/i)).toBeVisible()
    await expect(page).toHaveURL(/\/setup\/complete/)
  })
})

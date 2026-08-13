import { test, expect } from '@playwright/test'
import {
  mockApiRoutes,
  mockSetupCompany,
  mockSetupStatus,
  freezeTime,
} from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { clickButton, fillForm } from '../helpers/interactions'
import type {
  SetupCompanyResponse,
  SetupModelRecommendationsResponse,
} from '@/api/types/setup'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

/**
 * Critical-flow E2E: setup wizard.
 *
 * The wizard is a pure API consumer -- it persists NOTHING client-side, so
 * there is no localStorage seed to deep-link into a mid-wizard step. State
 * is hydrated from the backend on mount (``reconcileCompletionFromBackend``
 * reads ``GET /setup/status``), so these specs drive the wizard the way a
 * real operator does: mock the backend signals, then either deep-link a
 * post-company step (the backend reports it reachable) or pick the wizard
 * mode (which advances to the first incomplete step) for a pre-company one.
 */

test.describe('Setup wizard critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    await mockSetupStatus(page)
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

/** A fully-formed company the resume reconcile can hydrate from the backend. */
const COMPANY: SetupCompanyResponse = {
  company_name: 'E2E Test Co',
  description: null,
  template_applied: null,
  department_count: 1,
  agent_count: 1,
  agents: [
    {
      name: 'Ada',
      role: 'Engineer',
      department: 'engineering',
      model_provider: null,
      model_id: null,
      personality_preset: null,
      tier: 'medium',
    },
  ],
  currency: DEFAULT_CURRENCY,
  budget: 500,
  model_spend_profile: 'balanced',
}

// Per-feature model settings are MODEL_REF, so every recommendation and
// candidate carries the serialized provider-bound ref a settings write needs.
const MODEL_DEFAULT_REF = JSON.stringify({
  provider: 'test-provider',
  model_id: 'model-default',
})

const EMBED_DEFAULT_REF = JSON.stringify({
  provider: 'test-provider',
  model_id: 'embed-default',
})

/** Recommendations the Complete step's model panel loads on mount. */
const MODEL_RECS: SetupModelRecommendationsResponse = {
  charter_recommended: MODEL_DEFAULT_REF,
  cos_recommended: MODEL_DEFAULT_REF,
  decomposition_recommended: MODEL_DEFAULT_REF,
  embedding_candidates: [
    { provider: 'test-provider', model_id: 'embed-default', ref: EMBED_DEFAULT_REF },
  ],
  model_ref_candidates: [
    { provider: 'test-provider', model_id: 'model-default', ref: MODEL_DEFAULT_REF },
  ],
  narrative_recommended: MODEL_DEFAULT_REF,
  propose_recommended: MODEL_DEFAULT_REF,
  research_recommended: MODEL_DEFAULT_REF,
  routing_recommended: MODEL_DEFAULT_REF,
}

test.describe('Setup wizard company submit', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    // Providers already configured server-side: the reconcile marks the
    // Providers step complete, so picking Quick mode skips straight to the
    // (still-incomplete) Company step -- no company exists yet, so the form
    // renders rather than a preview.
    await mockSetupStatus(page, { has_providers: true })
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

    await page.goto('/setup/mode')
    // The reconcile fetches the provider list (``has_providers: true``) and
    // then marks the Providers step complete; waiting for that GET guarantees
    // the completion has landed before we select a mode, so Quick advances to
    // Company (not back to Providers).
    await page.waitForResponse(
      (res) =>
        /\/api\/v1\/providers(\?|$)/.test(res.url()) && res.request().method() === 'GET',
    )
    await expect(
      page.getByRole('heading', { name: /how would you like to set up/i }),
    ).toBeVisible()
    await page.getByRole('radio', { name: /quick setup/i }).click()

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

test.describe('Setup wizard complete step', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    // Everything before the final step exists server-side, so the reconcile
    // marks every prior step complete and the wizard resolves the Complete
    // step as the resume target -- a direct deep-link holds there.
    await mockSetupStatus(page, {
      has_providers: true,
      has_company: true,
      has_agents: true,
    })
    // The reconcile hydrates the company (``has_company: true``); without it
    // CompleteStep renders the skip-wizard fallback instead of the review UI.
    await mockSetupCompany(page, COMPANY)
    // CompleteStep renders the model-selection panel, which fetches its
    // recommendations on mount.
    await page.route('**/api/v1/setup/model-recommendations', (route) =>
      route.fulfill({ json: ok(MODEL_RECS) }),
    )
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

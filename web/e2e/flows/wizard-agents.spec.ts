import { test, expect } from '@playwright/test'
import {
  mockApiRoutes,
  mockSetupCompany,
  mockSetupStatus,
  freezeTime,
} from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import type {
  SetupAgentSummary,
  SetupCompanyResponse,
  SetupModelRecommendationsResponse,
} from '@/api/types/setup'
import { DEFAULT_CURRENCY } from '@/utils/currencies'

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
 *
 * The wizard persists nothing client-side, so the step is reached by
 * mocking the backend signals it hydrates from: a company + providers
 * exist server-side, so the (guided, default-mode) reconcile marks every
 * pre-Agents step complete and a deep-link to ``/setup/agents`` holds.
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

const AGENT: SetupAgentSummary = {
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
 * The Agents step embeds the Models section (WizardModelSelection), which
 * loads model recommendations on mount. An empty-candidates response is
 * enough: the section renders its (empty) pickers without crashing, and
 * the rename flow under test does not touch them.
 */
const MODEL_RECS: SetupModelRecommendationsResponse = {
  cos_recommended: null,
  decomposition_candidates: [],
  decomposition_recommended: null,
  embedding_candidates: [],
  embedding_recommended: null,
  embedding_recommended_dims: null,
  research_recommended: null,
}

/** The company the reconcile hydrates so the Agents step is reachable. */
const COMPANY: SetupCompanyResponse = {
  company_name: 'E2E Test Co',
  description: null,
  template_applied: null,
  department_count: 1,
  agent_count: 1,
  agents: [AGENT],
  currency: DEFAULT_CURRENCY,
  budget: 500,
  model_tier_profile: 'balanced',
}

test.describe('Setup wizard agents step', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    // Providers + company exist server-side; the (default) guided reconcile
    // marks mode / template / providers / company complete, so the Agents
    // step is the resume target and a deep-link to it holds.
    await mockSetupStatus(page, { has_providers: true, has_company: true })
    await mockSetupCompany(page, COMPANY)
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
    // The embedded Models section loads recommendations + the namespace
    // settings it prefills from. Without these the section gets a malformed
    // response and crashes the whole step into the error boundary.
    await page.route(/\/api\/v1\/setup\/model-recommendations(\?.*)?$/, (route) =>
      route.fulfill({ json: ok(MODEL_RECS) }),
    )
    await page.route(/\/api\/v1\/settings\/[a-z_]+(\?.*)?$/, (route) =>
      route.fulfill({ json: ok([]) }),
    )
  })

  test('renames an agent via the PUT round-trip', async ({ page }) => {
    await page.route('**/api/v1/setup/agents/0/name', (route) =>
      route.fulfill({ json: ok({ ...AGENT, name: 'Ada Lovelace' }) }),
    )

    await page.goto('/setup/agents')

    // The roster renders the seeded agent's card.
    await expect(page.getByRole('heading', { name: /customise your agents/i })).toBeVisible()
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

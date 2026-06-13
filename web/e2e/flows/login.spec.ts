import { test, expect, type Page } from '@playwright/test'
import { mockApiRoutes, freezeTime } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'
import { clickButton } from '../helpers/interactions'

/**
 * Critical-flow E2E: functional login.
 *
 * The ``visual/login.spec.ts`` suite only screenshots the login page;
 * this drives the real authentication state machine end-to-end:
 *   1. ``GuestGuard`` calls ``checkSession`` -> ``GET /auth/me`` (401)
 *      so the page renders the login form rather than redirecting.
 *   2. ``LoginPage`` fetches ``GET /setup/status`` to pick login vs
 *      first-run setup mode (``needs_admin: false`` -> login mode).
 *   3. On submit, ``POST /auth/login`` succeeds, ``performAuthFlow``
 *      re-fetches ``GET /auth/me`` (now 200) and flips ``authStatus`` to
 *      ``authenticated``; ``GuestGuard`` then redirects to the dashboard.
 *   4. The 401 negative path surfaces the error alert and keeps the user
 *      on the login form (no redirect).
 *
 * ``/auth/me`` is stateful: it 401s until ``/auth/login`` flips the
 * closure flag, then returns the user. That mirrors the real cookie
 * handshake (no session cookie until the login response sets one)
 * without a backend.
 */

const ADMIN_USER = {
  id: 'user-admin',
  username: 'admin',
  role: 'ceo',
  must_change_password: false,
  org_roles: ['owner'],
  scoped_departments: [],
}

const SETUP_STATUS_LOGIN_MODE = {
  has_agents: true,
  has_company: true,
  has_name_locales: true,
  has_providers: true,
  min_password_length: 12,
  needs_admin: false,
  needs_setup: false,
}

/** Wrap a payload in the dashboard's ``ApiResponse`` success envelope. */
function ok(data: unknown) {
  return { success: true, data, error: null, error_detail: null }
}

async function stubSetupStatus(page: Page): Promise<void> {
  await page.route('**/api/v1/setup/status', (route) =>
    route.fulfill({ json: ok(SETUP_STATUS_LOGIN_MODE) }),
  )
}

test.describe('Login critical flow', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await installWebSocketHarness(page)
    await mockApiRoutes(page)
    await stubSetupStatus(page)
  })

  test('authenticates and redirects to the dashboard', async ({ page }) => {
    // ``/auth/me`` returns 401 until the login POST flips this flag, then
    // returns the admin user -- the cookie handshake without a backend.
    let authenticated = false
    await page.route('**/api/v1/auth/me', (route) => {
      if (authenticated) {
        route.fulfill({ json: ok(ADMIN_USER) })
      } else {
        route.fulfill({ status: 401, json: { success: false, data: null, error: 'Not authenticated', error_detail: null } })
      }
    })
    await page.route('**/api/v1/auth/login', (route) => {
      authenticated = true
      route.fulfill({ json: ok({ expires_in: 3600 }) })
    })

    await page.goto('/login')
    await expect(page).toHaveURL(/\/login/)
    // Login mode renders the Sign In submit (not the setup "Create Account").
    const signIn = page.getByRole('button', { name: /sign in/i })
    await expect(signIn).toBeVisible()

    // Fill the password via the input type rather than by label: the
    // PasswordVisibilityGroup adds a "Show password" toggle whose
    // accessible name also matches /password/i, so a label query is
    // ambiguous. Login mode renders exactly one password input.
    await page.getByLabel('Username', { exact: true }).fill('admin')
    await page.locator('input[type="password"]').fill('correct horse battery staple')
    await clickButton(page, /sign in/i)

    // GuestGuard redirects the now-authenticated user to the dashboard.
    await expect(page).toHaveURL((url) => url.pathname === '/', { timeout: 10_000 })
    await expect(page.getByRole('button', { name: /sign in/i })).toHaveCount(0)
    await expect(page.locator('main')).toBeVisible()
  })

  test('shows an error and stays on the form when credentials are rejected', async ({ page }) => {
    await page.route('**/api/v1/auth/me', (route) =>
      route.fulfill({ status: 401, json: { success: false, data: null, error: 'Not authenticated', error_detail: null } }),
    )
    await page.route('**/api/v1/auth/login', (route) =>
      route.fulfill({
        status: 401,
        json: { success: false, data: null, error: 'Invalid username or password', error_detail: null },
      }),
    )

    await page.goto('/login')
    const signIn = page.getByRole('button', { name: /sign in/i })
    await expect(signIn).toBeVisible()

    await page.getByLabel('Username', { exact: true }).fill('admin')
    await page.locator('input[type="password"]').fill('wrong-password')
    await clickButton(page, /sign in/i)

    // The rejected login surfaces the error alert and does NOT redirect:
    // the Sign In button is still on screen and the URL is unchanged.
    await expect(page.getByRole('alert')).toBeVisible()
    await expect(page).toHaveURL(/\/login/)
    await expect(signIn).toBeVisible()
  })
})

import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime, waitForFonts } from '../fixtures/mock-api'
import { installWebSocketHarness } from '../fixtures/websocket-harness'

test.describe('Dashboard visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    // The WS harness keeps the stub socket open so the reconnect
    // machinery never exhausts and pops its transient toast into the
    // screenshot.
    await installWebSocketHarness(page)
    // mockApiRoutes stubs the cookie-session check (`GET /auth/me`) to a
    // non-401, so the app stays authenticated and never redirects to login;
    // no client-side token is involved (auth is HttpOnly-cookie based).
    await mockApiRoutes(page)
  })

  test('dashboard page screenshot', async ({ page }) => {
    await page.goto('/')
    await waitForFonts(page)
    // The dashboard renders no <h1> (page titles live in document.title);
    // the always-present Org Health section heading marks first paint.
    await expect(page.getByRole('heading', { name: 'Org Health' })).toBeVisible()
    await expect(page).toHaveScreenshot('dashboard.png')
  })
})

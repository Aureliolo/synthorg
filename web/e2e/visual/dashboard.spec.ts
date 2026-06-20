import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime, waitForFonts } from '../fixtures/mock-api'

test.describe('Dashboard visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    // mockApiRoutes stubs the cookie-session check (`GET /auth/me`) to a
    // non-401, so the app stays authenticated and never redirects to login;
    // no client-side token is involved (auth is HttpOnly-cookie based).
    await mockApiRoutes(page)
  })

  test('dashboard page screenshot', async ({ page }) => {
    await page.goto('/')
    await waitForFonts(page)
    await page.waitForSelector('h1')
    await expect(page).toHaveScreenshot('dashboard.png')
  })
})

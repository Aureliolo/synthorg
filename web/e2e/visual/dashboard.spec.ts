import { test, expect } from '@playwright/test'
import { mockApiRoutes, freezeTime, waitForFonts } from '../fixtures/mock-api'
import { seedAuth } from '../fixtures/auth'

test.describe('Dashboard visual regression', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await mockApiRoutes(page)
    // Bypass login: seed a mock token the auth store reads on init.
    await seedAuth(page)
  })

  test('dashboard page screenshot', async ({ page }) => {
    await page.goto('/')
    await waitForFonts(page)
    // Wait for content to load
    await page.waitForSelector('h1')
    await expect(page).toHaveScreenshot('dashboard.png')
  })
})

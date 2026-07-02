import { expect, test } from '@playwright/test'
import { freezeTime, mockApiRoutes, waitForFonts } from '../fixtures/mock-api'
import { mockIntegrationRoutes } from '../fixtures/integrations-mocks'

test.describe('Integrations dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    // mockApiRoutes stubs `GET /auth/me` to a non-401, keeping the app
    // authenticated (HttpOnly-cookie auth) so it never redirects to login.
    // It must be registered FIRST: Playwright matches routes LIFO, so the
    // integration-specific stubs below only win over mockApiRoutes'
    // ``**/api/v1/**`` catch-all when they are registered after it.
    await mockApiRoutes(page)
    await mockIntegrationRoutes(page)
  })

  test('Connections page loads with connections and health', async ({ page }) => {
    await page.goto('/connections')
    await waitForFonts(page)
    await expect(page.getByRole('heading', { name: 'Connections' })).toBeVisible()
    await expect(page.getByText('primary-github')).toBeVisible()
    await expect(page.getByText('dev-slack')).toBeVisible()
    await expect(page).toHaveScreenshot('connections-loaded.png', {
      fullPage: false,
      maxDiffPixelRatio: 0.02,
    })
  })

  test('Tunnel toggle starts and stops', async ({ page }) => {
    await page.goto('/connections')
    await waitForFonts(page)
    const toggle = page.getByRole('switch', { name: /start tunnel/i })
    await expect(toggle).toBeVisible()
    await toggle.click()
    // First enable shows the tunnel-intro explainer (the backend-owned
    // acknowledgement flag starts false); confirming starts the tunnel.
    // ConfirmDialog renders a Base UI AlertDialog (role=alertdialog).
    await expect(
      page.getByRole('alertdialog', { name: /about the webhook tunnel/i }),
    ).toBeVisible()
    await page.getByRole('button', { name: /I understand, start tunnel/i }).click()
    // Scope to the main region: the started-tunnel toast repeats the URL.
    const mainUrl = page
      .getByLabel('Main content')
      .getByText('mock-tunnel.trycloudflare.com')
    await expect(mainUrl).toBeVisible()
    const stopToggle = page.getByRole('switch', { name: /stop tunnel/i })
    await stopToggle.click()
    await expect(mainUrl).not.toBeVisible()
  })

  test('Tunnel provider picker shows credential states', async ({ page }) => {
    await page.goto('/connections')
    await waitForFonts(page)
    await page.getByRole('radio', { name: 'ngrok' }).click()
    await expect(page.getByLabel('Auth token')).toBeVisible()
    await page.getByRole('radio', { name: 'GitHub Dev Tunnels' }).click()
    await expect(page.getByText(/devtunnel CLI is not installed/i)).toBeVisible()
  })

  test('MCP Catalog browses and searches', async ({ page }) => {
    await page.goto('/integrations/mcp-catalog')
    await waitForFonts(page)
    await expect(page.getByRole('heading', { name: 'MCP Catalog' })).toBeVisible()
    // Card locators go through the accessible button name: bare
    // getByText('Filesystem') is ambiguous (name, description, and tag
    // chip all contain the word) and trips strict mode.
    await expect(page.getByRole('button', { name: 'View Filesystem' })).toBeVisible()
    await expect(page.getByRole('button', { name: 'View GitHub' })).toBeVisible()

    // The catalog search is an InputField labelled "Search" (a plain
    // textbox, not role=searchbox).
    await page.getByRole('textbox', { name: 'Search' }).fill('github')
    // The catalog store debounces search by 200ms; wait for
    // Filesystem to actually disappear instead of asserting
    // immediately (otherwise the test is flaky on fast machines).
    await page
      .getByRole('button', { name: 'View Filesystem' })
      .waitFor({ state: 'hidden' })
    await expect(page.getByRole('button', { name: 'View GitHub' })).toBeVisible()
  })

  test('Create connection flow opens the form and picks a type', async ({ page }) => {
    await page.goto('/connections')
    await waitForFonts(page)
    await page.getByRole('button', { name: /new connection/i }).click()
    await expect(page.getByRole('dialog', { name: /new connection/i })).toBeVisible()
    await page.getByRole('button', { name: /GitHub/ }).first().click()
    await expect(page.getByLabel('Connection name')).toBeVisible()
    await expect(page.getByLabel(/Personal Access Token/i)).toBeVisible()
  })
})

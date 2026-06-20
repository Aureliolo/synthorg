import { expect, test } from '@playwright/test'
import { freezeTime, mockApiRoutes, waitForFonts } from '../fixtures/mock-api'
import { mockIntegrationRoutes } from '../fixtures/integrations-mocks'
import { seedAuth } from '../fixtures/auth'

test.describe('Integrations dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await freezeTime(page)
    await mockIntegrationRoutes(page)
    await mockApiRoutes(page)
    await seedAuth(page)
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
    await expect(page.getByText('mock-tunnel.ngrok.io')).toBeVisible()
    const stopToggle = page.getByRole('switch', { name: /stop tunnel/i })
    await stopToggle.click()
    await expect(page.getByText('mock-tunnel.ngrok.io')).not.toBeVisible()
  })

  test('MCP Catalog browses and searches', async ({ page }) => {
    await page.goto('/integrations/mcp-catalog')
    await waitForFonts(page)
    await expect(page.getByRole('heading', { name: 'MCP Catalog' })).toBeVisible()
    await expect(page.getByText('Filesystem')).toBeVisible()
    await expect(page.getByText('GitHub')).toBeVisible()

    await page.getByRole('searchbox', { name: /search mcp catalog/i }).fill('github')
    // The catalog store debounces search by 200ms; wait for
    // Filesystem to actually disappear instead of asserting
    // immediately (otherwise the test is flaky on fast machines).
    await page.getByText('Filesystem').waitFor({ state: 'hidden' })
    await expect(page.getByText('GitHub')).toBeVisible()
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

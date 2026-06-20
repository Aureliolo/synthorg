import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: process.env.CI ? 1 : undefined,
  reporter: process.env.CI ? 'list' : 'html',

  // Per-test budget. A flow that opens a form, submits, and awaits the
  // API round-trip needs more headroom on a shared CI runner than the
  // 30s default; a too-tight budget reaps a still-pending
  // ``waitForResponse`` as a spurious failure rather than a real bug.
  timeout: process.env.CI ? 60_000 : 30_000,

  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
    // Keep a video of any attempt that fails, including the CI retry
    // (retries: 1), so a flake that only reproduces on the second run
    // still leaves a diagnostic artifact instead of discarding it.
    video: 'retain-on-failure-and-retries',
    reducedMotion: 'reduce',
    // Bound individual actions / navigations so a wedged click or load
    // fails fast inside the per-test budget instead of consuming it.
    actionTimeout: 15_000,
    navigationTimeout: 30_000,
  },

  expect: {
    // Web-first assertions (``toBeVisible`` etc.) retry up to this long;
    // the 5s default races slow first-paint under CI load.
    timeout: 10_000,
    toHaveScreenshot: {
      maxDiffPixelRatio: 0.01,
    },
  },

  projects: [
    {
      name: 'desktop-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'desktop-sm-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 1024, height: 768 } },
    },
    {
      name: 'tablet-chromium',
      use: { ...devices['Desktop Chrome'], viewport: { width: 768, height: 1024 } },
    },
    {
      name: 'desktop-firefox',
      use: { ...devices['Desktop Firefox'], viewport: { width: 1280, height: 800 } },
    },
    {
      name: 'desktop-webkit',
      use: { ...devices['Desktop Safari'], viewport: { width: 1280, height: 800 } },
    },
  ],

  webServer: {
    command: 'npm run preview',
    port: 4173,
    reuseExistingServer: !process.env.CI,
    // The preview server serves a freshly-built bundle; give it room to
    // boot on a cold CI runner before the first spec connects.
    timeout: 120_000,
  },
})

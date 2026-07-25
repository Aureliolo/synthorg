import { defineConfig, devices } from '@playwright/test'

const isCI = !!process.env['CI']

export default defineConfig({
  testDir: './e2e',
  outputDir: './test-results',
  fullyParallel: true,
  forbidOnly: isCI,
  retries: isCI ? 1 : 0,
  // Omitted off CI so Playwright keeps its own default (half the cores);
  // an explicit `undefined` is not the same thing under exactOptionalPropertyTypes.
  ...(isCI ? { workers: 1 } : {}),
  reporter: isCI ? 'list' : 'html',

  // Per-test budget. A flow that opens a form, submits, and awaits the
  // API round-trip needs more headroom on a shared CI runner than the
  // 30s default; a too-tight budget reaps a still-pending
  // ``waitForResponse`` as a spurious failure rather than a real bug.
  timeout: isCI ? 60_000 : 30_000,

  use: {
    baseURL: 'http://localhost:4173',
    trace: 'on-first-retry',
    // Keep a video of any attempt that fails, including the CI retry
    // (retries: 1), so a flake that only reproduces on the second run
    // still leaves a diagnostic artifact instead of discarding it.
    video: 'retain-on-failure-and-retries',
    // Reduced motion is a context option, not a top-level `use` key: set at
    // the top level it is silently dropped and every spec runs with full
    // animation, which is what makes visual snapshots race the transition.
    contextOptions: { reducedMotion: 'reduce' },
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
    reuseExistingServer: !isCI,
    // The preview server serves a freshly-built bundle; give it room to
    // boot on a cold CI runner before the first spec connects.
    timeout: 120_000,
  },
})

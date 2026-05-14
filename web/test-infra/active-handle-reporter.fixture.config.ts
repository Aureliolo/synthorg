import { defineConfig } from 'vitest/config'
import { fileURLToPath, URL } from 'node:url'

/**
 * Standalone vitest config used by the active-handle tracker
 * regression test in `web/src/__tests__/_infra/active-handle-reporter.test.ts`.
 *
 * The parent test spawns `vitest run --config <this file>` to drive
 * the deliberate-leak fixture (`web/src/__tests__/_infra/active-handle-reporter.fixture.ts`)
 * in isolation. The tracker is loaded as a setupFile so its
 * `afterEach` throws on the unallowed leaks the fixture creates.
 *
 * This config is intentionally minimal: no MSW, no jsdom, no React.
 * The fixture only exercises tracker behaviour.
 */
export default defineConfig({
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('../src', import.meta.url)),
    },
  },
  test: {
    name: 'active-handle-fixture',
    environment: 'node',
    setupFiles: [
      fileURLToPath(new URL('./active-handle-tracker.ts', import.meta.url)),
    ],
    // Vitest resolves glob patterns relative to the config's
    // ``root``; explicit ``root: ../`` puts us at the web/ project
    // root so the relative include below is unambiguous.
    root: fileURLToPath(new URL('..', import.meta.url)),
    include: ['src/__tests__/_infra/active-handle-reporter.fixture.ts'],
    reporters: ['default'],
  },
})

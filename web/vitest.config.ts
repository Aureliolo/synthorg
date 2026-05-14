import { defineConfig, configDefaults } from 'vitest/config'
import react from '@vitejs/plugin-react'
import codspeedPlugin from '@codspeed/vitest-plugin'
import { fileURLToPath, URL } from 'node:url'

import ActiveHandleReporter from './test-infra/active-handle-reporter'

// Two-project workspace:
//
//   * ``unit``  -- runs ``vitest run`` over ``*.test.ts(x)``, loads
//                  ``./src/test-setup.tsx`` (MSW, Motion mocks,
//                  toast teardown, theme listener teardown).
//   * ``bench`` -- runs ``vitest bench`` over ``*.bench.ts``, loads
//                  ``./src/bench-setup.ts`` (cookie shim only).
//
// Why projects: vitest 4's ``BenchmarkUserOptions`` has no
// ``setupFiles`` field. Before this split, ``test.setupFiles`` was
// shared with bench mode, which loaded MSW alongside every
// ``.bench.ts``. MSW's ``setupServer().listen()`` patches Node's
// global HTTP interceptor and trips an Invariant Violation on the
// second listen, failing the whole CodSpeed Web job. Project-scoped
// ``setupFiles`` is the supported / architectural fix; the model
// cannot accidentally regress it via a ``process.argv`` heuristic.
//
// CodSpeed plugin lives ON THE BENCH PROJECT ONLY. Declaring it at
// the root caused vitest's project resolver to teardown the
// plugin's globalSetup twice (once per inheriting project) and emit
// "teardown called twice" at end-of-run. The plugin is a no-op when
// ``process.env.CODSPEED`` is unset, so local ``npm run test`` /
// ``npm run bench`` are unaffected. Inside the CodSpeed CI runner
// it captures bench results for upload.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  test: {
    // Top-level ``coverage`` applies to the unit project (the only
    // project that runs under ``vitest run``); bench mode does not
    // emit coverage.
    coverage: {
      provider: 'v8',
      changed: process.env.CI ? undefined : 'origin/main',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.d.ts', 'src/main.tsx', 'src/__tests__/**'],
    },
    // Reporters are global, not per-project. ``ActiveHandleReporter``
    // wipes ``./.test-tmp`` at run start and reads the per-worker
    // NDJSON files at run end; the worker-side hooks are installed
    // by ``./test-infra/active-handle-tracker.ts`` as a setupFile on
    // the unit project below.
    reporters: ['default', new ActiveHandleReporter()],
    projects: [
      {
        extends: true,
        test: {
          name: 'unit',
          globals: true,
          environment: 'jsdom',
          // The active-handle tracker MUST load before ``./src/test-setup.tsx``
          // so its ``async_hooks`` hook is enabled before the MSW server,
          // motion mock, jsdom shims, and global ``beforeEach``/``afterEach``
          // hooks register. Its own snapshot/diff hooks then nest OUTSIDE the
          // test-setup hooks (outer-beforeEach-first / outer-afterEach-last),
          // so handles created in test-setup's ``beforeEach`` and torn down
          // in its ``afterEach`` are net-zero in the diff.
          setupFiles: [
            './test-infra/active-handle-tracker.ts',
            './src/test-setup.tsx',
          ],
          include: ['src/**/*.test.{ts,tsx}'],
          exclude: [...configDefaults.exclude, '**/e2e/**', '**/*.bench.ts'],
        },
      },
      {
        plugins: [codspeedPlugin()],
        extends: true,
        test: {
          name: 'bench',
          globals: true,
          environment: 'jsdom',
          setupFiles: ['./src/bench-setup.ts'],
          include: ['src/**/*.bench.ts'],
          exclude: [...configDefaults.exclude, '**/e2e/**'],
        },
      },
    ],
  },
})

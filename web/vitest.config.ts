import { defineConfig, configDefaults } from 'vitest/config'
import react from '@vitejs/plugin-react'
import codspeedPlugin from '@codspeed/vitest-plugin'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  // The CodSpeed plugin is a no-op when ``process.env.CODSPEED`` is
  // unset, so local ``npm run test`` / ``npm run bench`` are
  // unaffected. Inside the CodSpeed CI runner it captures bench
  // results for upload.
  plugins: [react(), codspeedPlugin()],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // ``test.setupFiles`` is shared between ``vitest run`` (test mode)
  // and ``vitest bench`` (benchmark mode). Vitest 4.x's bench command
  // inherits its setup from the parent ``test`` config, so the
  // synchronous ``document.cookie`` shim + global afterEach hooks in
  // ``test-setup.tsx`` apply to bench iterations too. This is what
  // keeps ``csrf.bench.ts`` from going through jsdom's tough-cookie
  // Promise-based getter and leaking unresolved Promises across
  // iterations.
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.tsx'],
    exclude: [...configDefaults.exclude, '**/e2e/**'],
    coverage: {
      provider: 'v8',
      changed: process.env.CI ? undefined : 'origin/main',
      include: ['src/**/*.{ts,tsx}'],
      exclude: ['src/**/*.d.ts', 'src/main.tsx', 'src/__tests__/**'],
    },
  },
})

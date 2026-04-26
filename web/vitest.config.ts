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

import type { KnipConfig } from 'knip'

/**
 * Knip configuration for the web dashboard.
 *
 * The `css` compiler lets Knip resolve `@import` package references in
 * `src/styles/*.css` (fonts, Tailwind, tw-animate-css). Without it those
 * packages look unused because Knip does not parse CSS.
 */
const config: KnipConfig = {
  entry: [
    'src/App.tsx',
    'src/**/*.stories.{ts,tsx}',
    'src/**/*.{test,spec,bench}.{ts,tsx}',
    'test-infra/**/*.ts',
    // Run only via a custom vitest config Knip cannot auto-detect; declaring
    // it as an entry also keeps its `leak-helpers` import reachable.
    'src/__tests__/_infra/active-handle-reporter.fixture.ts',
    // Ambient axios interceptor augmentation loaded by tsconfig, never imported.
    'src/__tests__/_types/axios-internal.d.ts',
  ],
  project: ['src/**/*.{ts,tsx}', 'src/**/*.css', 'test-infra/**/*.{ts,tsx}'],
  ignore: ['**/*.gen.ts'],
  // `uv` (Python toolchain) backs the api-types generate/check npm scripts.
  ignoreBinaries: ['uv'],
  // `openapi-typescript` runs as `npx openapi-typescript` inside
  // scripts/generate_dto_types_ts.py, a Python subprocess Knip cannot trace.
  ignoreDependencies: ['@types/.*', 'openapi-typescript'],
  // `<ComponentName>Props` interfaces are exported for greppability even when
  // only referenced in their own file (web/CLAUDE.md design-system rule).
  ignoreExportsUsedInFile: { interface: true, type: true },
  compilers: {
    css: (text: string) =>
      [...text.matchAll(/(?<=@)import[^;]+/g)].map((match) => match[0]).join('\n'),
  },
}

export default config

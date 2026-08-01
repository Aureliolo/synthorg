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
  // Generated modules are not project files, so nothing here can see an import
  // that reaches past a barrel straight into one. That gap is covered by the
  // `no-restricted-imports` pattern in eslint.config.js instead.
  ignore: ['**/*.gen.ts'],
  // `uv` (Python toolchain) backs the api-types generate/check npm scripts.
  ignoreBinaries: ['uv'],
  // `openapi-typescript` runs as `npx openapi-typescript` inside
  // scripts/generate_dto_types_ts.py, a Python subprocess Knip cannot trace.
  ignoreDependencies: ['openapi-typescript'],
  // `<ComponentName>Props` interfaces are exported for greppability even when
  // only referenced in their own file (web/CLAUDE.md design-system rule).
  ignoreExportsUsedInFile: { interface: true, type: true },
  // The `types` report is armed, and stays meaningful because a name has one
  // barrel: DTO shapes come from `@/api/types/<domain>` and nowhere else, so a
  // re-export nothing reaches through really is dead. The sole sanctioned
  // suppression is a `/** @public */` tag on a component sub-package barrel,
  // where the `<ComponentName>Props` beside an exported component is surface by
  // rule rather than by consumption. knip honours `@public` unconditionally
  // (`isAlwaysIgnored` in its `util/tag`), so no config is needed here.
  //
  // A failure here surfaces at pre-push before CI, which is the intended
  // ordering, not a coverage gap: `npm run lint:knip` also runs whole-dashboard
  // and unconditionally in the verify-backend.yml `dashboard-lint` job. The Gates job SKIPs
  // the `web-checks` hook only because it has no node toolchain, a mapping
  // `check_local_ci_parity.py` records in `_COVERED_ELSEWHERE` and enforces.
  compilers: {
    css: (text: string) =>
      [...text.matchAll(/@import\s+(?:url\()?['"]?([^'")]+)['"]?\)?/g)]
        .map((match) => `import '${match[1]}'`)
        .join('\n'),
  },
}

export default config

import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import eslintReact from '@eslint-react/eslint-plugin'
import { reactRefresh } from 'eslint-plugin-react-refresh'
import pluginSecurity from 'eslint-plugin-security'

// TODO: Add eslint-plugin-react-hooks when it supports ESLint 10 (v5 caps at ESLint 9).
// @eslint-react provides hooks analysis via the recommended-type-checked preset
// in the meantime (rules-of-hooks, exhaustive-deps, set-state-in-effect, etc.).

export default tseslint.config(
  // Generated artefacts are never linted. They live alongside the
  // hand-written sources but are produced by ``scripts/generate_*.py``
  // (the canonical regeneration commands live in ``web/CLAUDE.md``);
  // the four caps below (and every other rule) would only flag
  // unfixable issues there.
  { ignores: ['dist/**', '**/*.gen.ts', '**/*.gen.tsx'] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  eslintReact.configs['recommended-type-checked'],
  pluginSecurity.configs.recommended,
  {
    // Type-aware eslint-react rules (no-leaked-conditional-rendering,
    // dom-no-unknown-property, no-unused-state, static-components, etc.)
    // need the TypeScript project service so the rule implementations can
    // read inferred types. ``projectService: true`` auto-discovers the
    // nearest tsconfig per file so we don't have to enumerate the project
    // graph by hand.
    files: ['**/*.ts', '**/*.tsx'],
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    plugins: {
      'react-refresh': reactRefresh.plugin,
    },
    rules: {
      'react-refresh/only-export-components': [
        'warn',
        { allowConstantExport: true },
      ],
      'no-useless-assignment': 'error',
      'no-restricted-syntax': [
        'error',
        {
          selector: 'JSXAttribute[name.name="dangerouslySetInnerHTML"]',
          message:
            'dangerouslySetInnerHTML is banned -- use text content or a sanitization library. ' +
            'If absolutely necessary, add // eslint-disable-next-line no-restricted-syntax with a justification comment.',
        },
      ],
      // Rule flags every obj[var] with no data-flow analysis -- too many false
      // positives. Prototype pollution is guarded explicitly at system boundaries.
      'security/detect-object-injection': 'off',
      // -- eslint-react rules not in recommended-type-checked --
      // Prevent dollar signs from leaking into rendered JSX output
      '@eslint-react/jsx-no-leaked-dollar': 'error',
      // Remove unnecessary <></> fragment wrappers
      '@eslint-react/jsx-no-useless-fragment': 'warn',
      // Require type attribute on <button> to prevent unintended form submission
      '@eslint-react/dom-no-missing-button-type': 'warn',
      // Require rel="noopener" with target="_blank" (security)
      '@eslint-react/dom-no-unsafe-target-blank': 'error',
      // Catch duplicate keys in JSX lists
      '@eslint-react/no-duplicate-key': 'error',
      // Catch unstable context values that cause unnecessary re-renders
      '@eslint-react/no-unstable-context-value': 'warn',
      // Catch unstable default props that cause unnecessary re-renders
      '@eslint-react/no-unstable-default-props': 'warn',
      // -- v5 explicit opt-ins beyond the preset --
      // Detect fetch() in effects without AbortController cleanup. We use
      // axios via apiClient today, but the rule guards future raw fetch
      // call sites and matches the pattern this plugin's other
      // no-leaked-* rules cover.
      '@eslint-react/web-api-no-leaked-fetch': 'error',
      // Catch the {count && <Foo />} bug where a falsy 0 gets rendered
      // verbatim instead of nothing. Type-aware -- requires projectService.
      '@eslint-react/no-leaked-conditional-rendering': 'error',
      // Restrict global var usage (window, document, localStorage, etc.)
      // inside component render bodies. Hoist any new offenders into an
      // event handler / useEffect / useSyncExternalStore-backed hook
      // (see ``@/hooks/useViewportSize`` for the canonical pattern).
      '@eslint-react/globals': 'error',
      // Floating-promise + misused-promise hardening, complementary to
      // the active-handle reporter (``web/test-infra/active-handle-*``).
      // The reporter catches the runtime symptom (leaked Timeout /
      // socket); these rules catch the most common syntactic causes at
      // edit time so the bug never reaches the test run.
      //
      // ``checksVoidReturn.attributes: false`` follows the documented
      // typescript-eslint guidance for React codebases. The remaining
      // checks still fire on ``Array.forEach(asyncFn)``,
      // ``setTimeout(asyncFn, 0)``, etc.; React 19's global error
      // handler covers rejected async event handlers, and the
      // active-handle gate covers the runtime resource-leak symptom.
      '@typescript-eslint/no-floating-promises': 'error',
      '@typescript-eslint/no-misused-promises': [
        'error',
        { checksVoidReturn: { attributes: false } },
      ],
      // Tier-matched function / file size and complexity caps. Mirrors
      // the Python pylint thresholds (max-args 5, max-statements 30,
      // max-complexity 8) and the module-size tier table from
      // docs/decisions/0006-tiered-module-size-policy.md.
      complexity: ['error', 8],
      'max-lines': [
        'error',
        { max: 400, skipBlankLines: true, skipComments: true },
      ],
      'max-lines-per-function': [
        'error',
        { max: 80, skipBlankLines: true, skipComments: true, IIFEs: false },
      ],
      'max-params': ['error', 5],
    },
  },
  {
    // shadcn/ui components co-export variant helpers alongside components --
    // this is the standard pattern and safe for HMR.
    files: ['src/components/ui/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
      // Variants files (button-variants, sheet-variants, etc.) and many
      // shadcn primitives exceed 80 lines per function because they
      // are config-heavy by design (cva tuples). Variant components
      // pre-date the tier policy; new shadcn additions still respect
      // the cap.
      'max-lines-per-function': 'off',
    },
  },
  {
    // Test infra files compose many test setup variants; existing files
    // exceed the function-length cap.
    files: ['test-infra/**', '**/__tests__/**', '**/*.test.{ts,tsx}', '**/*.bench.ts'],
    rules: {
      complexity: 'off',
      'max-lines': 'off',
      'max-lines-per-function': 'off',
      'max-params': 'off',
    },
  },
)

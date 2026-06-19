import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import eslintReact from '@eslint-react/eslint-plugin'
import { reactRefresh } from 'eslint-plugin-react-refresh'
import pluginSecurity from 'eslint-plugin-security'
import reactHooks from 'eslint-plugin-react-hooks'

// eslint-plugin-react-hooks v7 supports ESLint 10 (peerDeps allow ^10), so the
// canonical rules-of-hooks + exhaustive-deps rule pair is wired below. We enable
// only those two (not the recommended-latest preset, which also pulls in the
// React-Compiler ``react-hooks/lints`` bundle -- this project does not run the
// compiler, and that bundle flags compiler-migration constraints rather than
// runtime bugs). ``@eslint-react/exhaustive-deps`` is retired in favour of the
// canonical rule; ``@eslint-react/rules-of-hooks`` stays on for redundant coverage.

export default tseslint.config(
  // Generated artefacts are never linted. They live alongside the
  // hand-written sources but are produced by ``scripts/generate_*.py``
  // (the canonical regeneration commands live in ``web/CLAUDE.md``);
  // the four caps below (and every other rule) would only flag
  // unfixable issues there.
  { ignores: ['dist/**', '**/*.gen.ts', '**/*.gen.tsx'] },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
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
    files: ['**/*.ts', '**/*.tsx'],
    plugins: {
      'react-hooks': reactHooks,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'error',
      // Retire the @eslint-react port now that the canonical rule is live: keeping
      // both would double-report and collide with the existing disable directives.
      '@eslint-react/exhaustive-deps': 'off',

      // react-hooks/lints (the recommended-latest React-Compiler bundle),
      // reconciled per rule. This project does NOT run the React Compiler, so
      // rules are split into genuine-runtime-correctness (error) and
      // compiler-migration-only constraints flagged on intentional patterns
      // (off, with the reason inline).
      //
      // Genuine runtime correctness -- all clean today, kept as ratchets:
      'react-hooks/set-state-in-render': 'error',
      'react-hooks/purity': 'error',
      'react-hooks/error-boundaries': 'error',
      'react-hooks/void-use-memo': 'error',
      'react-hooks/preserve-manual-memoization': 'error',
      'react-hooks/static-components': 'error',
      // Flags a non-inline function passed to useMemo (a real misuse the
      // compiler would also reject); kept at error.
      'react-hooks/use-memo': 'error',
      //
      // Compiler-migration-only / redundant -- off with the reason:
      // ``refs`` flags reads/writes of ``ref.current`` during render. Every
      // hit is the sanctioned stable-ref pattern (``usePolling`` family), the
      // React-documented render-phase ``prevRef`` prop-sync idiom, or
      // ``ctrl``-object taint false positives -- zero runtime bugs. It is also
      // mutually exclusive with ``set-state-in-effect``: the codebase satisfies
      // that rule by using the very render-phase prev-ref idiom ``refs`` forbids.
      'react-hooks/refs': 'off',
      // Both hits are false positives (a DOM ``scrollTop`` write and a
      // TDZ-safe ``usePolling`` closure), not React-state mutation.
      'react-hooks/immutability': 'off',
      // The react-hooks variant uniquely flags valid async data-fetch effects
      // (``void reload()``) that the already-enabled, clean
      // ``@eslint-react/set-state-in-effect`` correctly ignores. Keep the
      // @eslint-react port (below) as the single source of truth; leave this
      // off to avoid double-reporting on legitimate prop-sync sites.
      'react-hooks/set-state-in-effect': 'off',
      // React-Compiler config validators -- no-ops without the compiler.
      'react-hooks/config': 'off',
      'react-hooks/gating': 'off',
      // Compiler-only concerns (libraries / syntax the compiler cannot model);
      // no runtime meaning here.
      'react-hooks/incompatible-library': 'off',
      'react-hooks/unsupported-syntax': 'off',
      // Redundant with the already-enabled ``@eslint-react/globals`` (error).
      'react-hooks/globals': 'off',
    },
  },
  {
    files: ['**/*.ts', '**/*.tsx'],
    rules: {
      // -- strict adoption: deferred high-churn rules --
      // ``strictTypeChecked`` is adopted wholesale (above) for its full safety
      // surface, but three broad, near-cosmetic rules carry the bulk of the
      // live violation volume with low bug-yield. They are a DELIBERATE,
      // documented scope deferral (not a dodge of a promoted rule),
      // tracked for a follow-up hardening pass.
      // 788 violations / 300 files; purely stylistic (wrap ``() => fn()``).
      '@typescript-eslint/no-confusing-void-expression': 'off',
      // 677 violations / 143 files; replace ``x!`` with a real guard.
      '@typescript-eslint/no-non-null-assertion': 'off',
      // strictTypeChecked tightens this to forbid number/boolean/nullish
      // interpolation (390 violations / 169 files of pure churn). Relax back to
      // the permissive options so the rule still catches the genuine
      // ``${object}``/``${any}`` -> "[object Object]" bug (0 violations here)
      // without the numeric-template churn.
      '@typescript-eslint/restrict-template-expressions': [
        'error',
        { allowNumber: true, allowBoolean: true, allowNullish: true },
      ],
      // -- curated rules genuinely absent from strictTypeChecked --
      // ``== null`` / ``!= null`` is the deliberate nullish-check idiom across
      // the codebase; ``{ null: 'ignore' }`` permits it while still forbidding
      // every other loose comparison.
      eqeqeq: ['error', 'always', { null: 'ignore' }],
      // Forbid raw ``style="..."`` string props (must be an object).
      '@eslint-react/dom-no-string-style-prop': 'error',
      // Require an explicit ``sandbox`` on <iframe> (security).
      '@eslint-react/dom-no-missing-iframe-sandbox': 'error',
    },
  },
  {
    plugins: {
      'react-refresh': reactRefresh.plugin,
    },
    rules: {
      'react-refresh/only-export-components': [
        'error',
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
      // Rule flags every `===`/`!==` whose operand name looks secret-ish. Our
      // only matches are comparisons of monotonic request tokens / a user's own
      // password-confirm field -- neither is a timing-sensitive secret, so the
      // rule is a pure false-positive surface here.
      'security/detect-possible-timing-attacks': 'off',
      // -- eslint-react rules not in recommended-type-checked --
      // Prevent dollar signs from leaking into rendered JSX output
      '@eslint-react/jsx-no-leaked-dollar': 'error',
      // Remove unnecessary <></> fragment wrappers. Options pinned explicitly so a
      // future preset default flip cannot start erroring `<>{expr}</>` sites.
      '@eslint-react/jsx-no-useless-fragment': [
        'error',
        { allowEmptyFragment: false, allowExpressions: true },
      ],
      // Require type attribute on <button> to prevent unintended form submission
      '@eslint-react/dom-no-missing-button-type': 'error',
      // Require rel="noopener" with target="_blank" (security)
      '@eslint-react/dom-no-unsafe-target-blank': 'error',
      // Catch duplicate keys in JSX lists
      '@eslint-react/no-duplicate-key': 'error',
      // Catch unstable context values that cause unnecessary re-renders
      '@eslint-react/no-unstable-context-value': 'error',
      // Catch unstable default props that cause unnecessary re-renders
      '@eslint-react/no-unstable-default-props': 'error',
      // setState synchronously in an effect (derived-state smell). The preset
      // ships this at warn; promote to error explicitly.
      '@eslint-react/set-state-in-effect': 'error',
      // -- v5 explicit opt-ins beyond the preset --
      // Detect fetch() in effects without AbortController cleanup. We use
      // axios via apiClient today, but the rule guards future raw fetch
      // call sites and matches the pattern this plugin's other
      // no-leaked-* rules cover.
      '@eslint-react/web-api-no-leaked-fetch': 'error',
      // Detect IntersectionObserver / ResizeObserver created in effects
      // without a disconnect()/unobserve() cleanup. Direct siblings of
      // web-api-no-leaked-fetch; no observer call sites exist today, so
      // these are forward-looking ratchets that guard future additions.
      '@eslint-react/web-api-no-leaked-intersection-observer': 'error',
      '@eslint-react/web-api-no-leaked-resize-observer': 'error',
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

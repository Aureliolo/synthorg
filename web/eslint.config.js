import js from '@eslint/js'
import tseslint from 'typescript-eslint'
import eslintReact from '@eslint-react/eslint-plugin'
import { reactRefresh } from 'eslint-plugin-react-refresh'
import pluginSecurity from 'eslint-plugin-security'

// TODO: Add eslint-plugin-react-hooks when it supports ESLint 10 (v5 caps at ESLint 9).
// @eslint-react provides hooks analysis via the recommended-type-checked preset
// in the meantime (rules-of-hooks, exhaustive-deps, set-state-in-effect, etc.).

export default tseslint.config(
  { ignores: ['dist/**'] },
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
    },
  },
  {
    // shadcn/ui components co-export variant helpers alongside components --
    // this is the standard pattern and safe for HMR.
    files: ['src/components/ui/**'],
    rules: {
      'react-refresh/only-export-components': 'off',
    },
  },
)

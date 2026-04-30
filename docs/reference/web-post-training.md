---
title: Web Post-Training Reference
description: TypeScript 6 and Storybook 10 facts that post-date Claude's training cutoff. Read before generating tsconfig or Storybook config.
---

# Post-Training Reference (TypeScript 6 and Storybook 10)

These tools were released after Claude's training cutoff. Key facts for correct code generation.

## TypeScript 6.0

Reference: <https://aka.ms/ts6>.

- **`baseUrl` deprecated**: will stop working in TS 7. Remove it; `paths` entries are relative to the tsconfig directory.
- **`esModuleInterop` always true**: cannot be set to `false`; remove explicit `"esModuleInterop": true` to avoid the deprecation warning.
- **`types` defaults to `[]`**: no longer auto-discovers `@types/*`; must explicitly list needed types (e.g. `"types": ["vitest/globals"]`).
- **`DOM.Iterable` merged into `DOM`**: `"lib": ["ES2025", "DOM"]` is sufficient, no separate `DOM.Iterable`.
- **`moduleResolution: "classic"` and `"node10"` removed**: use `"bundler"` or `"nodenext"`.
- **`strict` defaults to `true`**: explicit `"strict": true` is redundant but harmless.
- **`noUncheckedSideEffectImports` defaults to `true`**: CSS side-effect imports need type declarations (Vite's `/// <reference types="vite/client" />` covers this).
- **Last JS-based TypeScript**: TS 7.0 will be rewritten in Go. Migration tool: `npx @andrewbranch/ts5to6`.

## Storybook 10

Reference: <https://storybook.js.org/docs/releases/migration-guide>.

- **ESM-only**: all CJS support removed.
- **Packages removed**: `@storybook/addon-essentials`, `@storybook/addon-interactions`, `@storybook/test`, `@storybook/blocks` no longer published. Essentials (backgrounds, controls, viewport, actions, toolbars, measure, outline) and interactions are built into core `storybook`.
- **`@storybook/addon-docs` is separate**: must be installed and added to addons if using `tags: ['autodocs']` or MDX.
- **Import paths changed**: use `storybook/test` (not `@storybook/test`), `storybook/actions` (not `@storybook/addon-actions`).
- **Type-safe config**: use `defineMain` from `@storybook/react-vite/node` and `definePreview` from `@storybook/react-vite` (must still include explicit `framework` field).
- **Backgrounds API changed**: use `parameters.backgrounds.options` (object keyed by name) + `initialGlobals.backgrounds.value` (replaces old `default` + `values` array).
- **a11y testing**: use `parameters.a11y.test: 'error' | 'todo' | 'off'` (replaces old `.element` and `.manual`). Set globally in `preview.tsx` to enforce WCAG compliance on all stories.
- **Minimum versions**: Node 20.19+, Vite 5+, Vitest 3+, TypeScript 4.9+.

## See also

- [web-design-system.md](web-design-system.md): component inventory (every component has a `.stories.tsx`).
- [web-base-ui-decisions.md](web-base-ui-decisions.md): which Base UI primitives the dashboard uses.

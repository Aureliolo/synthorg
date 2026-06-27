---
title: Web ESLint and TypeScript Strictness
description: The dashboard's strict TypeScript flags, the strictTypeChecked / no-unnecessary-condition fix patterns, error-level rule opt-ins, deferred high-churn rules, and the tiered complexity caps with the table-driven dispatch refactor pattern.
---

# Web ESLint and TypeScript Strictness

Lint runs via `npm --prefix web run lint` with `--max-warnings 0`. To enumerate stale `eslint-disable` directives after a rule reshuffle: `npm --prefix web run lint -- --report-unused-disable-directives-severity=warn`.

## TypeScript compiler strictness

`web/tsconfig.app.json` runs full strict mode plus `noUncheckedIndexedAccess`, `noPropertyAccessFromIndexSignature`, and **`exactOptionalPropertyTypes`**. Under the last, `prop?: T` is distinct from `prop: T | undefined`: forwarding an optional value is rejected unless the target also accepts `undefined`. Fix it the way `@types/react` does, by declaring presentational / internal optional props as `prop?: T | undefined`; at data / wire boundaries (request payloads, react-flow handles, lib.dom options) omit the key (conditional spread `...(x !== undefined && { x })`) or coerce (`x ?? null`, a real default) instead of widening. Never reach for `as` / `// @ts-expect-error` to silence it.

## strictTypeChecked + no-unnecessary-condition

`typescript-eslint` runs the **`strictTypeChecked`** preset and `@eslint-react/eslint-plugin` v5+ runs `recommended-type-checked` (both require `parserOptions.projectService: true` in `web/eslint.config.js`). `strictTypeChecked` pulls in `no-unnecessary-condition`: a flagged "always truthy/falsy" / "unnecessary optional chain" / "no overlap" is usually a genuinely dead check (delete it; for an exhaustive `Record<Enum, V>` the lookup is non-undefined and build-time exhaustiveness, not a `?? fallback`, is the protection). When the check is genuinely needed, prefer fixing the type over a suppression:

1. **Boundary type-lies** (`AxiosResponse.headers`/`.data` typed non-null yet absent on faked / coerced error objects, `navigator.clipboard` in insecure contexts, malformed-envelope fields, backend enum drift on a `Record` lookup): widen the value to its honest runtime type at the boundary by assigning to a `T | undefined` / untrusted-wire view local (or `Boolean(x)` for an unconstrained sentinel) so the guard is type-necessary.
2. **Effect cancellation**: use `createCancellationToken()` from `@/utils/cancellation`, whose `cancelled()` is a function call so a stale narrowing after an `await` cannot mask a cleanup-time `cancel()`.

The one case that still keeps a per-line `// eslint-disable-next-line @typescript-eslint/no-unnecessary-condition -- <why>` is a CFA-invisible closure mutation with no cleaner abstraction: a flag flipped inside a closure the flow analysis cannot follow (an applied-flag set inside a `set()` updater, an SSE `onProgress` capture, the module-level `shouldBeConnected` toggled by `disconnect()`, a `cancelledRef.current` flipped in effect cleanup). The value's type is correct and eslint's CFA is simply wrong, so the documented disable is the idiomatic fix.

## Error-level rule opt-ins (beyond the presets)

- `react-hooks/rules-of-hooks` + `react-hooks/exhaustive-deps` (`eslint-plugin-react-hooks` v7): the canonical hooks-dependency rule. `@eslint-react/exhaustive-deps` is **off** in favour of it, so a justified suppression uses `// eslint-disable-next-line react-hooks/exhaustive-deps -- <reason>`. The `react-hooks/lints` bundle (this app does NOT run the React Compiler) is reconciled per rule: genuine-runtime-correctness rules (`set-state-in-render`, `purity`, `error-boundaries`, `void-use-memo`, `preserve-manual-memoization`, `static-components`, `use-memo`) are **error**; compiler-migration-only / redundant rules (`refs`, `immutability`, the rh-variant `set-state-in-effect`, `config`, `gating`, `incompatible-library`, `unsupported-syntax`, `globals`) are **off** with an inline WHY. Prefer destructuring stable `useCallback`/`useRef` members over a mount-only `[]` + disable.
- `@eslint-react/web-api-no-leaked-fetch` / `-intersection-observer` / `-resize-observer`: detect `fetch()` / `IntersectionObserver` / `ResizeObserver` created in effects without the matching `AbortController` / `disconnect()` cleanup. The two observer rules are forward-looking ratchets.
- `@eslint-react/no-leaked-conditional-rendering`: catch the `{count && <Foo />}` bug where `0` renders. For `ReactNode | undefined` props use `{value != null && value !== false && <jsx>}`; for compound truthiness use `Boolean(...)`.
- `@eslint-react/globals`: restrict `window` / `document` / `localStorage` inside render. Hoist offenders into a `useCallback` handler, a `useEffect`, or a `useSyncExternalStore`-backed hook.
- `@typescript-eslint/no-floating-promises`: forbids unawaited promises so async work cannot survive the test that scheduled it and trip the active-handle gate.
- `@typescript-eslint/no-misused-promises` (with `checksVoidReturn: { attributes: false }`): forbids async functions where the callsite ignores the returned promise; React 19 `async` event handlers stay allowed via the exemption, paired with the global error handler.
- `no-constant-binary-expression` (with `checkRelationalComparisons: true`): the `js.configs.recommended` base rule already flags constant `===` / `&&` / `||` expressions; the option extends it to always-constant relational comparisons between two literals (`1 < 2`, `"a" >= "b"`). Zero current violations: a forward-looking correctness ratchet.
- Promoted from `warn` to `error` (codebase is clean): `@eslint-react/no-unstable-context-value`, `no-unstable-default-props`, `set-state-in-effect` (prop-to-local-state sync is the only exception, suppressed per-line with a reason), `jsx-no-useless-fragment` (options pinned), `dom-no-missing-button-type`, and `react-refresh/only-export-components` (`allowConstantExport`; still `off` for the `components/ui/**` shadcn variant co-exports).

## Deferred high-churn rules

`@typescript-eslint/no-confusing-void-expression` and `no-non-null-assertion` are `off`, and `restrict-template-expressions` is relaxed to `{ allowNumber, allowBoolean, allowNullish }` (it still catches the genuine `${object}`/`${any}` to `"[object Object]"` bug). These stay `off` because fixing them is high-churn for low bug-yield; each carries an inline WHY in `eslint.config.js`. Re-tighten only when a rule's cost-to-signal ratio changes.

## Tiered caps + per-bucket ratchet

The four caps (`complexity: 8`, `max-lines: 400`, `max-lines-per-function: 80`, `max-params: 5`) mirror the Python pylint thresholds and the module-size tier table in `docs/decisions/0006-tiered-module-size-policy.md`. They apply to all `**/*.{ts,tsx}`. Two narrow exemptions in `web/eslint.config.js`: `src/components/ui/**` disables only `max-lines-per-function` (cva config-heavy variants; new shadcn additions still respect the cap), and the test/bench globs disable all four. All `web/src/` production code respects the caps.

The canonical refactor for keeping a function under `complexity: 8` is **table-driven dispatch**: replace a multi-arm `if`/`switch` ladder with a `Readonly<Record<K, V>>` lookup, optionally `as const satisfies Record<K, V>` for exhaustiveness. Worked examples: `utils/errors.ts` (`CONFLICT_MESSAGES`, `CATEGORY_TITLES`, ...), `utils/provider-status.ts` (`_hasRequiredCredentials` exhaustive `Record<AuthType, boolean>`), `hooks/use-list-shortcuts.ts` (`KEY_TO_ACTION`), `hooks/useToolbarKeyboardNav.ts` (`TOOLBAR_INDEX_FNS`). For an over-long body extract per-stage helpers (`utils/fetch-with-retry.ts`); for an oversized hook extract sub-hooks (`hooks/usePolling.ts::usePollRefs`, `hooks/useAgentDetailData.ts`). Sub-hook names MUST start with `use` (rules-of-hooks); no leading underscore.

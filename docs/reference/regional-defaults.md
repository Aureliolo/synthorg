---
title: Regional Defaults
description: How SynthOrg enforces the no-default-region rule for currency, locale, timezone, units, and spelling across the backend, frontend, and CI surfaces.
---

# Regional Defaults

On-demand reference. The short rule in `CLAUDE.md` is: no default may privilege a single region, currency, or locale. Every user-facing format resolves from user/company setting -> browser/system -> neutral fallback. This page is the enforcement-script reference and per-line opt-out guide.

## Resolution chain

| Surface | Source order |
|---|---|
| Currency (backend) | `budget.currency` runtime setting > `DEFAULT_CURRENCY` from `synthorg.budget.currency` |
| Currency (frontend) | `useSettingsStore().currency` > `DEFAULT_CURRENCY` from `@/utils/currencies` |
| Locale (backend) | `Intl` with the system locale; no operator-tunable backend locale setting (the company `name_locales` list controls procedural-name generation, not number / date formatting) |
| Locale (frontend) | `getLocale()` from `@/utils/locale` > browser default > `'en'` |
| Timezone | UTC stored everywhere; render via `Intl` without passing a `timeZone` option (browser tz wins) |
| Number / date format | `Intl` with the resolved locale; never hand-rolled templates |
| Units | Metric only |
| Spelling | International / British English UI default (`colour`, `behaviour`, `organise`, `centred`, `analyse`, `cancelled`); document deviations |

## What's enforced

Two PostToolUse hooks run on every Claude Code edit; one CI gate runs in pre-push and GitHub Actions for non-Claude commits.

### Frontend: `scripts/check_web_design_system.py`

PostToolUse hook on every `web/src/` `Edit`/`Write`. Flags:

- Hardcoded ISO 4217 currency codes (`'USD'`, `'EUR'`, `'GBP'`, ...) outside the allowlisted files (`web/src/utils/currencies.ts` and the `DEFAULT_CURRENCY` re-export in `format.ts`).
- Currency symbols adjacent to digits in user-facing strings (`"$10"`, `"€50"`).
- Identifiers ending in `_usd` (the field carries the operator's configured currency; the type carries money semantics, not the name).
- BCP 47 locale literals (`'en-US'`, `'de-DE'`, ...) anywhere in production code.
- Bare `.toLocaleString()` / `.toLocaleDateString()` / `.toLocaleTimeString()` calls; use the helpers in `@/utils/format` which all read `getLocale()`.
- `localhost:<port>` in application code.

Hardcoded hex colors, font-family literals, pixel spacing, Motion durations, and missing Storybook stories are also flagged by this same hook (see `web/CLAUDE.md`).

### Backend: `scripts/check_backend_regional_defaults.py`

PostToolUse hook on every `src/synthorg/` `Edit`/`Write`. Flags:

- Hardcoded ISO 4217 currency codes outside the allowlisted file (`budget/currency.py` symbol table).
- Currency symbols adjacent to digits in user-facing strings.
- Identifiers ending in `_usd` (backend, API DTOs, Pydantic types, DB columns).
- BCP 47 locale literals.
- `localhost:<port>` in application code.

### CI gate: `scripts/check_forbidden_literals.py`

Runs in pre-push and GitHub Actions. Same detection set as the two PostToolUse hooks but covers diffs from non-Claude commits (manual git, agents that bypass the hook, Renovate, ...). The pre-push hook fails the push if any new occurrence is introduced; the CI job fails the build.

### CI gate: `scripts/check_currency_aggregation_invariant.py`

Runs in pre-push. AST-walks `src/synthorg/` for unguarded aggregations over currency-bearing attributes (`cost`, `amount`, `total_cost`, `usd`, `eur`) -- specifically `sum(...)`, `math.fsum(...)`, `statistics.mean(...)`, and `statistics.fmean(...)` whose first argument is a generator / list / set comprehension whose `elt` is one of those attributes. A preceding call to `assert_currencies_match` (or the legacy `assert_single_currency`) in the same enclosing function or module clears the violation. Per-line opt-out: `# lint-allow: currency-aggregation -- <reason>` (mandatory non-empty justification). The gate's job is to catch the FIRST regression after the convention has been rolled out; the helper itself lives in `synthorg.budget.currency`.

## Per-line opt-out

Legitimate opt-outs use one of:

- Python: `# lint-allow: regional-defaults`
- TypeScript: `// lint-allow: regional-defaults`

The marker can be placed on the offending line or the line directly above. It opts out of all four detection categories on that line; do not stack multiple markers per line.

For the currency-aggregation invariant the marker is separate: `# lint-allow: currency-aggregation -- <reason>` with a mandatory non-empty justification.

## Allowlisted files

These files contain the canonical literals and are exempted from currency / locale literal detection:

- `src/synthorg/budget/currency.py`: symbol table + `DEFAULT_CURRENCY`.
- `web/src/utils/currencies.ts`: dropdown options + `DEFAULT_CURRENCY`.
- `web/src/utils/format.ts`: `DEFAULT_CURRENCY` re-export.
- `web/public/provider-logos/*.svg`: provider logo filenames mirror provider preset names by necessity.

## Monetary models invariant

Every cost-bearing Pydantic model carries `currency: CurrencyCode` (ISO 4217, validated against the allowlist in `synthorg.budget.currency`). The current models are:

- `CostRecord`
- `TaskMetricRecord`
- `LlmCalibrationRecord`
- `AgentRuntimeState`

Every aggregation site enforces a same-currency invariant; mixing currencies raises `MixedCurrencyAggregationError` (HTTP 409, error code `4007`). The aggregation sites are `CostTracker`, `ReportGenerator`, `CostOptimizer`, and HR `WindowMetrics`.

## See also

- [errors.md](errors.md) §"Conflict (4xxx)" -- `MIXED_CURRENCY_AGGREGATION` (4007).
- `web/CLAUDE.md` Design System section -- the design-token side of the same hook.

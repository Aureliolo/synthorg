# Internationalization

SynthOrg's UI text is shipped in **International / British English**. UI text translation and right-to-left (RTL) layout support are not currently planned. Locale-aware **display** of numbers, dates, times, currencies, and units is unaffected by this decision: those still flow through `Intl` and resolve via the operator's user / company / browser / system fallback (see [Regional Defaults in CLAUDE.md](https://github.com/Aureliolo/synthorg/blob/main/CLAUDE.md)).

## Decision

The dashboard ships UI copy in International / British English (`colour`, `behaviour`, `organise`, `centred`, `analyse`). Strings are inline in components; there is no `t()` resolver, no message catalog, and no locale switcher. Currency / date / time / number formatting continues to be locale-resolved at runtime through the existing helpers; only the UI text variant is fixed.

## What this means in practice

- Component authors keep user-facing strings inline. Do not extract them into a constants module "for centralization." That has indirection cost without payoff while the product remains English-only.
- The narrow exception is when the same long error message is used three or more times verbatim. That is deduplication, not internationalization; keep it close to the consumers (e.g. a small `errors.ts` next to the components).
- Currency, date, and number formatting still flow through `Intl` and read the operator's settings (see [Regional Defaults in CLAUDE.md](https://github.com/Aureliolo/synthorg/blob/main/CLAUDE.md)). That is locale-aware *display* of numeric data, not translation of the surrounding UI chrome.
- Audit findings flagging "hardcoded user-facing strings should be centralized for i18n readiness" are no longer tracked. The codebase audit's agent definition has been updated accordingly.

## When to revisit

This decision is reversible. The trigger is a real, scoped requirement: a specific market, a specific customer engagement, or a regulatory translation mandate. If that arrives, open a new issue with the requirement in the body. The implementation path would then be a real translation framework (likely react-intl or i18next), not a string-constants module.
